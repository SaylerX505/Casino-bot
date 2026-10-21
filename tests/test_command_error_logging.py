""""Regression tests for per-guild command-error logging configured by /wizard."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import main


class CommandErrorLogRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_configured_guild_channel_without_pinging_users(self):
        channel = SimpleNamespace(guild=SimpleNamespace(id=202), send=AsyncMock())
        ctx = SimpleNamespace(
            command=SimpleNamespace(qualified_name="test-command"),
            guild=SimpleNamespace(id=202),
            channel=SimpleNamespace(id=303),
            author=SimpleNamespace(id=404),
        )

        with (
            patch("main.configured_log_channel_id", return_value=505) as resolve_channel,
            patch("main.bot.get_channel", return_value=channel),
            patch("main._send_command_error", new=AsyncMock()),
        ):
            await main.on_command_error(ctx, RuntimeError("private exception contents"))

        resolve_channel.assert_called_once_with(
            202,
            "command_errors",
            main.ERROR_LOG_CHANNEL_ID,
        )
        channel.send.assert_awaited_once()
        kwargs = channel.send.await_args.kwargs
        self.assertNotIn("content", kwargs)
        self.assertEqual(kwargs["allowed_mentions"], discord.AllowedMentions.none())
        self.assertNotIn("private exception contents", kwargs["embed"].description)
        self.assertIn("RuntimeError", kwargs["embed"].description)

    async def test_never_forwards_errors_to_a_different_guild(self):
        channel = SimpleNamespace(guild=SimpleNamespace(id=999), send=AsyncMock())
        ctx = SimpleNamespace(
            command=SimpleNamespace(qualified_name="test-command"),
            guild=SimpleNamespace(id=202),
            channel=SimpleNamespace(id=303),
            author=SimpleNamespace(id=404),
        )

        with (
            patch("main.configured_log_channel_id", return_value=505),
            patch("main.bot.get_channel", return_value=channel),
            patch("main._send_command_error", new=AsyncMock()),
        ):
            await main.on_command_error(ctx, RuntimeError("private exception contents"))

        channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
