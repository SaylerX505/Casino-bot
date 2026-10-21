"""Regression tests for per-guild command-error logging configured by /wizard."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import main


def view_text(view):
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


class CommandErrorLogRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_error_uses_configured_channel_without_pinging_or_leaking_details(self):
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
        mentions = kwargs["allowed_mentions"]
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.replied_user)
        self.assertNotIn("private exception contents", view_text(kwargs["view"]))
        self.assertIn("RuntimeError", view_text(kwargs["view"]))

    async def test_slash_error_uses_the_same_configured_log_channel(self):
        channel = SimpleNamespace(guild=SimpleNamespace(id=202), send=AsyncMock())
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            command=SimpleNamespace(qualified_name="test slash"),
            user=SimpleNamespace(id=404),
            guild_id=202,
            channel=SimpleNamespace(id=303),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

        with (
            patch("main.configured_log_channel_id", return_value=505),
            patch("main.bot.get_channel", return_value=channel),
        ):
            await main.on_app_command_error(interaction, RuntimeError("private exception contents"))

        channel.send.assert_awaited_once()
        self.assertIn("RuntimeError", view_text(channel.send.await_args.kwargs["view"]))
        self.assertNotIn(
            "private exception contents",
            view_text(channel.send.await_args.kwargs["view"]),
        )
        response.send_message.assert_awaited_once()
        self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])

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
