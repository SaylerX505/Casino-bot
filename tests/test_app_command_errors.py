"""Regression tests for private, single-acknowledgement command error responses."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utility.theme import StatusPanel
from main import bot, on_app_command_error, on_command_error


def fake_interaction(*, acknowledged=False):
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name="admin.status"),
        user=SimpleNamespace(id=101),
        guild_id=202,
        channel=SimpleNamespace(id=303),
        response=SimpleNamespace(
            is_done=Mock(return_value=acknowledged),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def view_text(view):
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


class AppCommandErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_check_failure_uses_v2_panel_without_ephemeral_kwarg(self):
        ctx = SimpleNamespace(interaction=None, send=AsyncMock())

        await on_command_error(ctx, commands.CheckFailure("denied"))

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertNotIn("ephemeral", kwargs)
        self.assertIn("صلاحيات", view_text(kwargs["view"]))

    async def test_hybrid_check_failure_uses_private_interaction_response(self):
        interaction = fake_interaction()
        ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())

        await on_command_error(ctx, commands.CheckFailure("denied"))

        interaction.response.send_message.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertIn("صلاحيات", view_text(kwargs["view"]))
        ctx.send.assert_not_awaited()

    async def test_already_acknowledged_hybrid_error_uses_followup_once(self):
        interaction = fake_interaction(acknowledged=True)
        ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())

        await on_command_error(ctx, commands.CheckFailure("denied"))

        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertIsInstance(interaction.followup.send.await_args.kwargs["view"], StatusPanel)

    async def test_prefix_error_does_not_echo_exception_or_user_message(self):
        ctx = SimpleNamespace(
            interaction=None,
            command=SimpleNamespace(qualified_name="dangerous"),
            author=SimpleNamespace(id=303),
            guild=None,
            channel=SimpleNamespace(id=404),
            message=SimpleNamespace(content="private input that must not be forwarded"),
            send=AsyncMock(),
        )

        with patch("main._forward_error_to_guild_log", new=AsyncMock()):
            await on_command_error(ctx, RuntimeError("sensitive exception detail"))

        ctx.send.assert_awaited_once()
        visible_view = ctx.send.await_args.kwargs["view"]
        visible_message = view_text(visible_view)
        self.assertIsInstance(visible_view, StatusPanel)
        self.assertNotIn("private input", visible_message)
        self.assertNotIn("sensitive exception detail", visible_message)
        self.assertIn("رقم المتابعة", visible_message)
        self.assertNotIn("ephemeral", ctx.send.await_args.kwargs)

    async def test_cross_guild_error_is_not_forwarded_to_another_guild(self):
        log_channel = SimpleNamespace(guild=SimpleNamespace(id=999), send=AsyncMock())
        ctx = SimpleNamespace(
            interaction=None,
            command=SimpleNamespace(qualified_name="dangerous"),
            author=SimpleNamespace(id=303),
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=404),
            message=SimpleNamespace(content="private input"),
            send=AsyncMock(),
        )

        with patch.object(bot, "get_channel", return_value=log_channel):
            await on_command_error(ctx, RuntimeError("do not send cross-guild"))

        log_channel.send.assert_not_awaited()
        ctx.send.assert_awaited_once()

    async def test_check_failure_is_acknowledged_ephemerally(self):
        interaction = fake_interaction()

        await on_app_command_error(interaction, app_commands.CheckFailure("denied"))

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        self.assertIsInstance(interaction.response.send_message.await_args.kwargs["view"], StatusPanel)
        self.assertIn("صلاحيات", view_text(interaction.response.send_message.await_args.kwargs["view"]))
        interaction.followup.send.assert_not_awaited()

    async def test_unexpected_failure_uses_followup_after_acknowledgement(self):
        interaction = fake_interaction(acknowledged=True)

        await on_app_command_error(interaction, RuntimeError("internal details"))

        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertNotIn("internal details", view_text(kwargs["view"]))
        self.assertIn("رقم المتابعة", view_text(kwargs["view"]))


if __name__ == "__main__":
    unittest.main()
