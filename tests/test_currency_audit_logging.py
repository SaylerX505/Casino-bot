"""Regression tests for single-guild economy audit logging."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.casino.currency import Currency


class EconomyAuditLogTests(unittest.IsolatedAsyncioTestCase):
    def build_cog(self, channel):
        bot = SimpleNamespace(get_channel=Mock(return_value=channel), fetch_channel=AsyncMock())
        return Currency(bot), bot

    async def test_never_sends_transfer_log_to_a_different_guild(self):
        channel = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            send=AsyncMock(),
        )
        cog, _ = self.build_cog(channel)

        await cog.log_action(
            SimpleNamespace(id=222),
            "Transfer",
            "member transfer",
            discord.Color.orange(),
        )

        channel.send.assert_not_awaited()

    async def test_sends_log_when_channel_belongs_to_originating_guild(self):
        channel = SimpleNamespace(guild=SimpleNamespace(id=111), send=AsyncMock())
        cog, _ = self.build_cog(channel)

        await cog.log_action(
            SimpleNamespace(id=111),
            "Transfer",
            "member transfer",
            discord.Color.orange(),
        )

        channel.send.assert_awaited_once()
        self.assertIsInstance(channel.send.await_args.kwargs["embed"], discord.Embed)
        self.assertEqual(channel.send.await_args.kwargs["allowed_mentions"], discord.AllowedMentions.none())


if __name__ == "__main__":
    unittest.main()
