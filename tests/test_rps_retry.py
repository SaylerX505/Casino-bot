"""Tests for retrying RPS escrow settlement after transient SQLite failures."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ui import LayoutView

from cogs.casino.rps import RPSView, _settle_wager


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


if __name__ == "__main__":
    unittest.main()
