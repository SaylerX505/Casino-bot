"""Regression tests for closing stale solo-wager panels after startup recovery."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ui import LayoutView

from cogs.casino.solo_betting import _publish_interrupted_wager_panel


class InterruptedWagerPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_refunded_wager_replaces_old_controls_with_a_clear_receipt(self):
        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot = SimpleNamespace(
            wait_until_ready=AsyncMock(),
            get_channel=lambda channel_id: channel if channel_id == 300 else None,
            fetch_channel=AsyncMock(),
        )
        record = {
            "game_id": "old-game",
            "game_type": "coinflip",
            "stake": 100,
            "channel_id": 300,
            "message_id": 400,
        }

        await _publish_interrupted_wager_panel(
            bot,
            record,
            {"status": "refunded", "outcome": "refund", "stake": 100},
        )

        bot.wait_until_ready.assert_awaited_once()
        channel.fetch_message.assert_awaited_once_with(400)
        bot.fetch_channel.assert_not_awaited()
        message.edit.assert_awaited_once()
        panel = message.edit.await_args.kwargs["view"]
        self.assertIsInstance(panel, LayoutView)
        text = "\n".join(
            item.content for item in panel.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("# ROUND REFUNDED", text)
        self.assertIn("Bet returned:", text)
        self.assertIn("100", text)
        self.assertIn("COIN FLIP", text)

    async def test_unsettled_or_winning_receipt_does_not_mark_a_game_refunded(self):
        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot = SimpleNamespace(
            wait_until_ready=AsyncMock(),
            get_channel=lambda _channel_id: channel,
            fetch_channel=AsyncMock(),
        )
        record = {
            "game_id": "active-game",
            "game_type": "coinflip",
            "stake": 100,
            "channel_id": 300,
            "message_id": 400,
        }

        await _publish_interrupted_wager_panel(
            bot,
            record,
            {"status": "settled", "outcome": "win", "payout": 190},
        )
        await _publish_interrupted_wager_panel(
            bot,
            record,
            {"status": "active", "outcome": None},
        )

        bot.wait_until_ready.assert_not_awaited()
        channel.fetch_message.assert_not_awaited()
        message.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
