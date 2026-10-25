"""Tests for retrying RPS escrow settlement after transient SQLite failures."""

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import discord
from discord.ui import LayoutView

from cogs.casino.rps import (
    _RECOVERY_TASKS,
    RPS,
    RPSView,
    _schedule_wager_recovery,
    _settle_wager,
)
from cogs.utility.theme import StatusPanel


class RPSComponentsV2Tests(unittest.TestCase):
    def test_match_panel_uses_three_secondary_v2_buttons(self):
        player = SimpleNamespace(id=101, mention="<@101>")
        opponent = SimpleNamespace(id=202, mention="<@202>")
        view = RPSView(SimpleNamespace(), player, opponent, 100, "game-ui-test")

        self.assertIsInstance(view, LayoutView)
        buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
        self.assertEqual(
            {button.label for button in buttons},
            {"قبول التحدي", "رفض", "إلغاء التحدي"},
        )
        self.assertIn("100", view._panel_text.content)
        self.assertIn("<@101>", view._panel_text.content)
        self.assertIn("<@202>", view._panel_text.content)
        self.assertIn("مفيش رصيد اتحجز", view._panel_text.content)

        view.accepted = True
        view._refresh_panel()
        move_buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual({button.label for button in move_buttons}, {"حجر", "ورقة", "مقص"})
        self.assertIn("الرهان المحجوز", view._panel_text.content)

        view.choices[player.id] = "🪨"
        view._refresh_panel()
        self.assertIn("✅ اختار", view._panel_text.content)
        move_buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual(sum(button.disabled for button in move_buttons), 0)

        view._disable_buttons()
        self.assertTrue(all(button.disabled for button in move_buttons))


class RPSAbsoluteDeadlineTests(unittest.IsolatedAsyncioTestCase):
    def make_view(self):
        player = SimpleNamespace(id=101, mention="<@101>")
        opponent = SimpleNamespace(id=202, mention="<@202>")
        return RPSView(SimpleNamespace(), player, opponent, 100, "deadline-test")

    async def test_absolute_deadline_expires_without_any_component_click(self):
        view = self.make_view()
        finish = AsyncMock()

        with patch.object(view, "finish", new=finish):
            view.schedule_deadline(0.01)
            await asyncio.sleep(0.03)

        finish.assert_awaited_once_with()
        self.assertIsNone(view._deadline_task)
        view.stop()

    async def test_acceptance_after_absolute_deadline_never_reserves_stakes(self):
        view = self.make_view()
        view.deadline = time.monotonic() - 1
        interaction = SimpleNamespace(user=SimpleNamespace(id=view.opponent.id))

        with (
            patch.object(view, "_defer_private", new=AsyncMock(return_value=True)),
            patch.object(view, "_private_notice", new=AsyncMock()) as notice,
            patch("cogs.casino.rps.bank.accept_rps_challenge") as accept,
        ):
            await view.accept_challenge(interaction)

        accept.assert_not_called()
        self.assertFalse(view.accepted)
        notice.assert_awaited_once()
        self.assertIn("انتهت مهلة القبول", notice.await_args.args[1])
        view.stop()

    async def test_private_notice_uses_components_v2_even_after_defer(self):
        view = self.make_view()
        interaction = SimpleNamespace(
            response=SimpleNamespace(is_done=lambda: True),
            edit_original_response=AsyncMock(),
        )

        await view._private_notice(interaction, "مش مسموح بالضغط ده.")

        interaction.edit_original_response.assert_awaited_once()
        kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertIsNone(kwargs["content"])
        self.assertEqual(kwargs["embeds"], [])
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in view.walk_children() if isinstance(button, discord.ui.Button)))
        view.stop()


class RPSettlementRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_settlement_runs_in_a_worker_thread(self):
        main_thread = threading.get_ident()
        transaction_threads = []

        def settle(game_id, winner_id):
            transaction_threads.append(threading.get_ident())
            return {"settled": True, "status": "settled", "game_id": game_id, "winner_id": winner_id}

        with patch("cogs.casino.rps.bank.settle_rps_wager", side_effect=settle):
            result = await _settle_wager("worker-thread-test", 101)

        self.assertTrue(result["settled"])
        self.assertEqual(len(transaction_threads), 1)
        self.assertNotEqual(transaction_threads[0], main_thread)

    async def test_transient_database_errors_are_retried_then_settled(self):
        settled = {"settled": True, "status": "settled", "payout": 190, "tax": 10}
        with (
            patch(
                "cogs.casino.rps.bank.settle_rps_wager",
                side_effect=[RuntimeError("busy"), RuntimeError("busy"), settled],
            ) as settle,
            patch("cogs.casino.rps.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            result = await _settle_wager("game-retry", 101)

        self.assertEqual(result, settled)
        self.assertEqual(settle.call_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_persistent_database_error_fails_closed_after_three_attempts(self):
        with (
            patch("cogs.casino.rps.bank.settle_rps_wager", side_effect=RuntimeError("busy")) as settle,
            patch("cogs.casino.rps.asyncio.sleep", new=AsyncMock()),
        ):
            result = await _settle_wager("game-fails", 101)

        self.assertFalse(result["settled"])
        self.assertEqual(result["reason"], "database_error")
        self.assertEqual(settle.call_count, 3)


class RPSBackgroundRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_retries_until_settled_and_deduplicates_workers(self):
        pending = {"settled": False, "reason": "database_error"}
        recovered = {
            "settled": True,
            "status": "refunded",
            "payout": 0,
            "tax": 0,
        }
        on_recovered = AsyncMock()

        with (
            patch(
                "cogs.casino.rps._settle_wager",
                side_effect=[pending, recovered],
            ) as settle,
            patch("cogs.casino.rps.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            first = _schedule_wager_recovery("background-recovery-test", on_recovered=on_recovered)
            second = _schedule_wager_recovery("background-recovery-test", on_recovered=on_recovered)
            self.assertIs(first, second)
            await first

        self.assertEqual(settle.await_count, 2)
        self.assertEqual(sleep.await_count, 2)
        on_recovered.assert_awaited_once_with(recovered)
        self.assertNotIn("background-recovery-test", _RECOVERY_TASKS)

    async def test_recovery_stops_if_the_escrow_record_does_not_exist(self):
        on_recovered = AsyncMock()
        with (
            patch(
                "cogs.casino.rps._settle_wager",
                return_value={"settled": False, "reason": "not_found"},
            ) as settle,
            patch("cogs.casino.rps.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            task = _schedule_wager_recovery("missing-record-test", on_recovered=on_recovered)
            await task

        settle.assert_awaited_once_with("missing-record-test", None)
        sleep.assert_awaited_once_with(5)
        on_recovered.assert_not_awaited()
        self.assertNotIn("missing-record-test", _RECOVERY_TASKS)


class RPSStartupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_recovery_failure_disables_new_wagers_without_crashing_load(self):
        cog = RPS(SimpleNamespace())
        with (
            patch(
                "cogs.casino.rps.bank.active_rps_wager_records",
                side_effect=OSError("database unavailable"),
            ),
            patch("cogs.casino.rps.bank.expire_pending_rps_challenges", return_value=[]),
            patch("cogs.casino.rps.bank.refund_orphaned_rps_wagers") as refund,
        ):
            await cog.cog_load()

        refund.assert_not_called()

        self.assertFalse(cog._escrow_ready)


    async def test_startup_refunds_and_closes_a_previously_active_challenge_panel(self):
        bot = SimpleNamespace()
        record = {
            "game_id": "interrupted-rps",
            "player_a": "101",
            "player_b": "202",
            "amount": 100,
            "channel_id": 303,
            "message_id": 404,
        }
        status = {
            "game_id": "interrupted-rps",
            "player_a": "101",
            "player_b": "202",
            "amount": 100,
            "status": "refunded",
            "outcome": "refund",
            "channel_id": 303,
            "message_id": 404,
        }
        publish = AsyncMock()
        with (
            patch("cogs.casino.rps.bank.expire_pending_rps_challenges", return_value=[]),
            patch("cogs.casino.rps.bank.active_rps_wager_records", return_value=[record]),
            patch("cogs.casino.rps.bank.refund_orphaned_rps_wagers", return_value=1) as refund,
            patch("cogs.casino.rps.bank.active_rps_wager_ids", return_value=[]),
            patch("cogs.casino.rps.bank.rps_wager_status", return_value=status),
            patch("cogs.casino.rps._publish_interrupted_rps_panel", new=publish),
        ):
            cog = RPS(bot)
            await cog.cog_load()
            await asyncio.sleep(0)

        refund.assert_called_once_with()
        self.assertTrue(cog._escrow_ready)
        publish.assert_awaited_once_with(bot, record, status)

    async def test_startup_expires_unaccepted_challenge_and_closes_its_panel(self):
        bot = SimpleNamespace()
        pending = {
            "game_id": "unaccepted-rps",
            "player_a": "101",
            "player_b": "202",
            "amount": 100,
            "channel_id": 303,
            "message_id": 404,
        }
        publish = AsyncMock()
        with (
            patch("cogs.casino.rps.bank.expire_pending_rps_challenges", return_value=[pending]),
            patch("cogs.casino.rps.bank.active_rps_wager_records", return_value=[]),
            patch("cogs.casino.rps.bank.refund_orphaned_rps_wagers", return_value=0),
            patch("cogs.casino.rps.bank.active_rps_wager_ids", return_value=[]),
            patch("cogs.casino.rps._publish_expired_rps_challenge_panel", new=publish),
        ):
            cog = RPS(bot)
            await cog.cog_load()
            await asyncio.sleep(0)

        self.assertTrue(cog._escrow_ready)
        publish.assert_awaited_once_with(bot, pending)


    async def test_startup_schedules_retries_for_escrow_left_active(self):
        cog = RPS(SimpleNamespace())
        with (
            patch("cogs.casino.rps.bank.expire_pending_rps_challenges", return_value=[]),
            patch("cogs.casino.rps.bank.active_rps_wager_records", return_value=[]),
            patch("cogs.casino.rps.bank.refund_orphaned_rps_wagers", return_value=2),
            patch("cogs.casino.rps.bank.active_rps_wager_ids", return_value=["pending-a", "pending-b"]),
            patch("cogs.casino.rps._schedule_wager_recovery") as schedule,
        ):
            await cog.cog_load()

        self.assertTrue(cog._escrow_ready)
        self.assertEqual(schedule.call_count, 2)
        schedule.assert_any_call("pending-a")
        schedule.assert_any_call("pending-b")




class RPSChoiceConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_first_choice_edit_cannot_overwrite_the_final_result(self):
        player = SimpleNamespace(id=101, mention="<@101>")
        opponent = SimpleNamespace(id=202, mention="<@202>")
        message = SimpleNamespace(edit=AsyncMock())
        view = RPSView(
            SimpleNamespace(send=AsyncMock()),
            player,
            opponent,
            100,
            "race-game",
        )
        view.message = message
        view.accepted = True
        view._refresh_panel()

        ack_started = asyncio.Event()
        release_ack = asyncio.Event()

        async def pause_first_ack(*args, **kwargs):
            ack_started.set()
            await release_ack.wait()

        first_response = SimpleNamespace(
            send_message=AsyncMock(side_effect=pause_first_ack),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        )
        second_state = {"done": False}

        async def defer_second(*args, **kwargs):
            second_state["done"] = True

        second_response = SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(side_effect=defer_second),
            edit_message=AsyncMock(),
            edit_original_response=AsyncMock(),
            is_done=lambda: second_state["done"],
        )
        first_interaction = SimpleNamespace(user=player, response=first_response)
        second_interaction = SimpleNamespace(
            user=opponent,
            response=second_response,
            edit_original_response=AsyncMock(),
        )

        with patch(
            "cogs.casino.rps._settle_wager",
            return_value={
                "settled": True,
                "status": "refunded",
                "payout": 0,
                "tax": 0,
            },
        ) as settle:
            first_task = asyncio.create_task(view.choose(first_interaction, "🪨"))
            await asyncio.wait_for(ack_started.wait(), timeout=1)
            second_task = asyncio.create_task(view.choose(second_interaction, "🪨"))

            # The second click must wait until the first non-terminal panel edit ends.
            await asyncio.sleep(0)
            self.assertFalse(view.done)
            self.assertNotIn(opponent.id, view.choices)

            release_ack.set()
            await asyncio.gather(first_task, second_task)

        settle.assert_awaited_once_with("race-game")
        first_response.send_message.assert_awaited_once()
        second_response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        message.edit.assert_has_awaits([call(view=view), unittest.mock.call(view=view)])
        self.assertEqual(message.edit.await_count, 2)
        second_response.edit_message.assert_not_awaited()
        second_interaction.edit_original_response.assert_awaited_once()
        acknowledgement = second_interaction.edit_original_response.await_args.kwargs
        self.assertIsNone(acknowledgement["content"])
        self.assertEqual(acknowledgement["embeds"], [])
        self.assertEqual(acknowledgement["attachments"], [])
        self.assertIsInstance(acknowledgement["view"], StatusPanel)
        mentions = acknowledgement["allowed_mentions"]
        self.assertIsInstance(mentions, discord.AllowedMentions)
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.replied_user)
        self.assertTrue(view.done)
        self.assertIn("تعادل", view._panel_text.content)
        self.assertTrue(all(button.disabled for button in view._buttons))

if __name__ == "__main__":
    unittest.main()
