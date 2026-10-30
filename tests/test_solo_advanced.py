"""Regression tests for the new interactive and instant solo betting games."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.casino import solo_advanced, solo_betting
from cogs.casino.permissions import GAME_COMMAND_ALIASES, GAME_LABELS


class SoloAdvancedGameTests(unittest.TestCase):
    def test_mines_multiplier_starts_at_one_and_grows_after_safe_reveals(self):
        self.assertEqual(solo_advanced._mines_multiplier(3, 0), 1.0)
        self.assertGreater(solo_advanced._mines_multiplier(3, 1), 1.0)
        self.assertGreater(
            solo_advanced._mines_multiplier(3, 4),
            solo_advanced._mines_multiplier(3, 3),
        )

    def test_mines_multiplier_rejects_invalid_ranges(self):
        for mines_count, safe_reveals in ((0, 0), (9, 0), (3, -1), (3, 18)):
            with self.subTest(mines_count=mines_count, safe_reveals=safe_reveals), self.assertRaises(ValueError):
                solo_advanced._mines_multiplier(mines_count, safe_reveals)

    def test_crash_point_is_finite_and_within_advertised_bounds(self):
        for _ in range(500):
            point = solo_advanced._crash_point()
            self.assertGreaterEqual(point, 1.0)
            self.assertLessEqual(point, solo_advanced.CRASH_MAX_MULTIPLIER)
            self.assertEqual(round(point, 2), point)

    def test_crash_point_clamps_instant_crash_and_maximum(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=0):
            self.assertEqual(solo_advanced._crash_point(), 1.0)
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=999_999):
            self.assertEqual(
                solo_advanced._crash_point(),
                solo_advanced.CRASH_MAX_MULTIPLIER,
            )

    def test_plinko_extreme_bin_has_three_times_gross_return(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=0):
            result, won, payout = solo_advanced._plinko_drop("drop", 100)
        self.assertEqual(result, "slot=0;multiplier=3x")
        self.assertTrue(won)
        self.assertEqual(payout, 300)

    def test_plinko_center_bin_loses_the_stake(self):
        # Cumulative weights before the center bin are 1 + 8 + 28 + 56 = 93.
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=93):
            result, won, payout = solo_advanced._plinko_drop("drop", 100)
        self.assertEqual(result, "slot=4;multiplier=0x")
        self.assertFalse(won)
        self.assertEqual(payout, 0)

    def test_number_roulette_wins_only_on_the_exact_number(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=17):
            result, won, payout = solo_advanced._number_roulette("17", 100)
        self.assertEqual(result, "17")
        self.assertTrue(won)
        self.assertEqual(payout, 3_600)

        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=17):
            result, won, payout = solo_advanced._number_roulette("18", 100)
        self.assertEqual(result, "17")
        self.assertFalse(won)
        self.assertEqual(payout, 0)

    def test_number_roulette_accepts_zero_and_rejects_out_of_range(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=0):
            result, won, payout = solo_advanced._number_roulette("0", 10)
        self.assertEqual(result, "0")
        self.assertTrue(won)
        self.assertEqual(payout, 360)

        for choice in ("-1", "37", "not-a-number", "١٧"):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                solo_advanced._number_roulette(choice, 100)

    def test_new_games_are_registered_for_channel_policy(self):
        expected = {
            "mines": "Mines",
            "crash": "Crash",
            "plinko": "Plinko",
            "number_roulette": "روليت الأرقام",
            "hilo": "Hi-Lo",
        }
        for key, label in expected.items():
            with self.subTest(game=key):
                self.assertEqual(GAME_LABELS[key], label)
        for command, key in (
            ("mines", "mines"),
            ("crash", "crash"),
            ("plinko", "plinko"),
            ("numberroulette", "number_roulette"),
            ("hilo", "hilo"),
        ):
            with self.subTest(command=command):
                self.assertEqual(GAME_COMMAND_ALIASES[command], key)

    def test_game_art_generates_png_for_each_new_game(self):
        for game_type in ("mines", "crash", "hilo"):
            with self.subTest(game_type=game_type):
                image = solo_advanced._render_live_art(game_type)
                self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        for game_type in ("plinko", "number_roulette"):
            with self.subTest(game_type=game_type):
                image = solo_betting._render_advanced_instant_art(game_type)
                self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


class FinalGameArtworkStateTests(unittest.TestCase):
    def setUp(self):
        context = SimpleNamespace(author=SimpleNamespace(id=778899))
        self.crash = solo_advanced.CrashView(None, context, 100)
        self.hilo = solo_advanced.HiLoView(None, context, 100)

    def test_mines_push_still_reveals_terminal_board(self):
        context = SimpleNamespace(author=SimpleNamespace(id=445566))
        view = solo_advanced.MinesView(None, context, 100, 3)
        view.receipt = {
            "status": "settled",
            "outcome": "push",
        }

        with patch(
            "cogs.casino.solo_advanced._render_live_art",
            return_value=b"png",
        ) as render:
            view._render_art()

        self.assertTrue(render.call_args.kwargs["reveal_mines"])
        view._on_finished(view.receipt)
        mine_index = next(iter(view.mines))
        self.assertEqual(str(view.tile_buttons[mine_index].emoji), "💣")
        self.assertTrue(view.tile_buttons[mine_index].disabled)


    def test_crash_cashout_does_not_render_a_false_crash_point(self):
        self.crash.receipt = {
            "status": "settled",
            "outcome": "win",
        }
        self.crash.result_multiplier = 2.5
        with patch(
            "cogs.casino.solo_advanced._render_live_art",
            return_value=b"png",
        ) as render:
            self.crash._render_art()

        self.assertEqual(render.call_args.kwargs["crash_point"], None)
        self.assertTrue(render.call_args.kwargs["final"])
        self.assertIs(render.call_args.kwargs["won"], True)

    def test_crash_refund_does_not_render_as_a_loss(self):
        self.crash.receipt = {
            "status": "refunded",
            "outcome": "refund",
        }
        with patch(
            "cogs.casino.solo_advanced._render_live_art",
            return_value=b"png",
        ) as render:
            self.crash._render_art()

        self.assertIsNone(render.call_args.kwargs["crash_point"])
        self.assertIsNone(render.call_args.kwargs["won"])

    def test_hilo_refund_does_not_render_as_a_loss(self):
        self.hilo.receipt = {
            "status": "refunded",
            "outcome": "refund",
        }
        with patch(
            "cogs.casino.solo_advanced._render_live_art",
            return_value=b"png",
        ) as render:
            self.hilo._render_art()

        self.assertTrue(render.call_args.kwargs["final"])
        self.assertIsNone(render.call_args.kwargs["won"])


class InteractiveWagerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _make_context(self, user_id=12345):
        author = SimpleNamespace(id=user_id)
        message = SimpleNamespace(id=98765, edit=AsyncMock(return_value=None))
        context = SimpleNamespace(
            author=author,
            channel=SimpleNamespace(id=54321),
            send=AsyncMock(return_value=message),
        )
        interaction = SimpleNamespace(
            user=author,
            message=message,
            response=SimpleNamespace(
                edit_message=AsyncMock(return_value=None),
                send_message=AsyncMock(return_value=None),
                is_done=lambda: False,
            ),
        )
        return context, interaction, message

    async def test_opening_panel_does_not_debit_before_player_starts(self):
        context, interaction, _ = self._make_context()
        view = solo_advanced.MinesView(None, context, 100, 3)

        with patch.object(solo_advanced.bank, "create_casino_wager") as create:
            self.assertTrue(await view.start())
            self.assertFalse(view._wager_created)
            create.assert_not_called()

            await view.cancel_before_start(interaction)

            create.assert_not_called()

        self.assertTrue(view.done)
        self.assertFalse(view._wager_created)

    async def test_cancelled_creation_waits_before_attempting_refund(self):
        creation_started = threading.Event()
        allow_creation_to_finish = threading.Event()

        def slow_create(*args, **kwargs):
            creation_started.set()
            if not allow_creation_to_finish.wait(timeout=5):
                raise TimeoutError("test creation was not released")
            return {"created": True, "balance": 900}

        context, interaction, _ = self._make_context()
        view = solo_advanced.MinesView(None, context, 100, 3)
        refund_receipt = {
            "settled": True,
            "already_settled": False,
            "status": "refunded",
            "outcome": "refund",
            "payout": 0,
            "balance_after": 1_000,
            "result": "pending",
        }

        with (
            patch.object(
                solo_advanced.bank,
                "create_casino_wager",
                side_effect=slow_create,
            ),
            patch.object(
                solo_advanced.bank,
                "settle_casino_wager",
                return_value=refund_receipt,
            ) as settle,
        ):
            await view.start()
            start_task = asyncio.create_task(view.begin_game(interaction))
            self.assertTrue(await asyncio.to_thread(creation_started.wait, 2))
            start_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await start_task

            self.assertIsNotNone(view._settlement_task)
            allow_creation_to_finish.set()
            await asyncio.wait_for(view._settlement_task, timeout=3)

        self.assertTrue(view.done)
        settle.assert_called_once_with(view.game_id, force_refund=True)

    async def test_post_escrow_panel_failure_requests_refund(self):
        context, interaction, _ = self._make_context()
        view = solo_advanced.MinesView(None, context, 100, 3)
        refund_receipt = {
            "settled": True,
            "already_settled": False,
            "status": "refunded",
            "outcome": "refund",
            "payout": 0,
            "balance_after": 1_000,
            "result": "pending",
        }

        def fail_active_panel(rows):
            raise RuntimeError("simulated active-panel render failure after escrow")

        with (
            patch.object(
                solo_advanced.bank,
                "create_casino_wager",
                return_value={"created": True, "balance": 900},
            ),
            patch.object(
                solo_advanced.bank,
                "settle_casino_wager",
                return_value=refund_receipt,
            ) as settle,
        ):
            await view.start()
            view._render_panel = fail_active_panel
            await view.begin_game(interaction)
            self.assertIsNotNone(view._settlement_task)
            await view._settlement_task

        self.assertTrue(view.done)
        settle.assert_called_once_with(view.game_id, force_refund=True)


class LegacyStyleParityTests(unittest.TestCase):
    def test_multistep_games_share_the_legacy_v2_panel_structure(self):
        requester = SimpleNamespace(id=111222)
        legacy = solo_betting.SoloWagerView(
            None,
            requester,
            100,
            game_type="coinflip",
            game_title="COIN FLIP",
            choices=solo_betting.SIDES,
            context=None,
        )
        context = SimpleNamespace(author=requester)
        new_views = (
            solo_advanced.MinesView(None, context, 100, 3),
            solo_advanced.CrashView(None, context, 100),
            solo_advanced.HiLoView(None, context, 100),
        )

        self.assertEqual(type(legacy.children[0]).__name__, "Container")
        legacy_children = legacy.children[0].children
        legacy_shape = [type(item).__name__ for item in legacy_children[:3]]
        self.assertEqual(legacy_shape, ["TextDisplay", "MediaGallery", "Separator"])

        for view in new_views:
            with self.subTest(game=view.game_type):
                self.assertEqual(type(view.children[0]).__name__, "Container")
                children = view.children[0].children
                self.assertEqual(
                    [type(item).__name__ for item in children[:3]],
                    legacy_shape,
                )
                self.assertEqual(len(view.children[0].children), len(legacy_children))



class LegacySoloPresentationTests(unittest.TestCase):
    def test_multistep_opening_copy_matches_legacy_and_keeps_start_controls(self):
        requester = SimpleNamespace(id=998877)
        context = SimpleNamespace(author=requester)
        views = (
            solo_advanced.MinesView(None, context, 100, 3),
            solo_advanced.CrashView(None, context, 100),
            solo_advanced.HiLoView(None, context, 100),
        )
        try:
            for view in views:
                with self.subTest(game=view.game_type):
                    self.assertEqual(
                        view._initial_content(),
                        f"# {view.game_title}\n**الرهان:** 100 {solo_betting.COIN}",
                    )
                    self.assertEqual(view.start_button.label, "ابدأ الجولة")
                    self.assertEqual(view.cancel_button.label, "إلغاء")
                    self.assertIn(view.start_button, view.children[0].children[-1].children)
        finally:
            for view in views:
                view.stop()

    def test_multistep_final_receipts_use_compact_legacy_copy(self):
        requester = SimpleNamespace(id=998878)
        context = SimpleNamespace(author=requester)
        view = solo_advanced.MinesView(None, context, 100, 3)
        try:
            cases = (
                ("win", "settled", "# كسبت · Mines"),
                ("loss", "settled", "# خسرت · Mines"),
                ("refund", "refunded", "# تم إرجاع الرصيد · Mines"),
                ("push", "settled", "# تم استرداد الرهان · Mines"),
            )
            for outcome, status, heading in cases:
                with self.subTest(outcome=outcome):
                    view.receipt = {
                        "status": status,
                        "outcome": outcome,
                        "balance_after": 900,
                        "payout": 190,
                    }
                    view._pending_display_result = "نتيجة الجولة."
                    content = view._final_content()
                    self.assertTrue(content.startswith(heading))
                    self.assertIn(f"**رصيدك:** 900 {solo_betting.COIN}", content)
                    self.assertNotIn("إجمالي الصرف", content)
                    self.assertNotIn("صافي المكسب", content)
                    self.assertNotIn("الرهان المفقود", content)
        finally:
            view.stop()


if __name__ == "__main__":
    unittest.main()
