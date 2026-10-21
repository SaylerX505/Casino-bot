"""Regression tests for slash-command error acknowledgement."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from discord import app_commands
from discord.ext import commands

from main import bot, on_app_command_error, on_command_error


def fake_interaction(*, acknowledged=False):
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name="admin.status"),
        user=SimpleNamespace(id=101),
        guild_id=202,
        response=SimpleNamespace(
            is_done=Mock(return_value=acknowledged),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class AppCommandErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_check_failure_does_not_pass_ephemeral_kwarg(self):
        ctx = SimpleNamespace(
            interaction=None,
            send=AsyncMock(),
        )

        await on_command_error(ctx, commands.CheckFailure("denied"))

        ctx.send.assert_awaited_once_with("❌ الأمر ده مش متاح لصلاحيات حسابك.")

    async def test_hybrid_check_failure_uses_ephemeral_interaction_response(self):
        ctx = SimpleNamespace(
            interaction=object(),
            send=AsyncMock(),
        )

        await on_command_error(ctx, commands.CheckFailure("denied"))

        ctx.send.assert_awaited_once_with(
            "❌ الأمر ده مش متاح لصلاحيات حسابك.",
            ephemeral=True,
        )

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

        with unittest.mock.patch.object(bot, "get_channel", return_value=None):
            await on_command_error(ctx, RuntimeError("sensitive exception detail"))

        ctx.send.assert_awaited_once()
        visible_message = ctx.send.await_args.args[0]
        self.assertNotIn("private input", visible_message)
        self.assertNotIn("sensitive exception detail", visible_message)
        self.assertNotIn("ephemeral", ctx.send.await_args.kwargs)

    async def test_cross_guild_error_is_not_forwarded_to_another_guild(self):
        log_channel = SimpleNamespace(
            guild=SimpleNamespace(id=999),
            send=AsyncMock(),
        )
        ctx = SimpleNamespace(
            interaction=None,
            command=SimpleNamespace(qualified_name="dangerous"),
            author=SimpleNamespace(id=303),
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=404),
            message=SimpleNamespace(content="private input"),
            send=AsyncMock(),
        )

        with unittest.mock.patch.object(bot, "get_channel", return_value=log_channel):
            await on_command_error(ctx, RuntimeError("do not send cross-guild"))

        log_channel.send.assert_not_awaited()
        ctx.send.assert_awaited_once()

    async def test_check_failure_is_acknowledged_ephemerally(self):
        interaction = fake_interaction()

        await on_app_command_error(interaction, app_commands.CheckFailure("denied"))

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        self.assertIn("صلاحيات", interaction.response.send_message.await_args.args[0])
        interaction.followup.send.assert_not_awaited()

    async def test_unexpected_failure_uses_followup_after_acknowledgement(self):
        interaction = fake_interaction(acknowledged=True)

        await on_app_command_error(interaction, RuntimeError("internal details"))

        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertNotIn(
            "internal details",
            interaction.followup.send.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
