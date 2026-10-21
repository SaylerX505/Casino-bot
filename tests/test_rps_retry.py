"""Tests for retrying RPS escrow settlement after transient SQLite failures."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ui import LayoutView

from cogs.casino.rps import (
    RPSView,
    _RECOVERY_TASKS,
    _schedule_wager_recovery,
    _settle_wager,
)


class RPSComponentsV2Tests(unittest.TestCase):
    def test_match_panel_uses_three_secondary_v2_buttons(self):
        player = SimpleNamespace(id=101, mention="<@101>")
        opponent = SimpleNamespace(id=202, mention="<@202>")
        view = RPSView(SimpleNamespace(), player, opponent, 100, "game-ui-test")

        self.assertIsInstance(view, LayoutView)
        buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
        self.assertIn("100", view._panel_text.content)
        self.assertIn("<@101>", view._panel_text.content)
        self.assertIn("<@202>", view._panel_text.content)

        view.choices[player.id] = "🪨"
        view._refresh_panel()
        self.assertIn("✅", view._panel_text.content)
        self.assertEqual(sum(button.disabled for button in buttons), 0)

        view._disable_buttons()
        self.assertTrue(all(button.disabled for button in buttons))


class RPSettlementRetryTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
