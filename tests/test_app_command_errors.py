"""Regression tests for slash-command error acknowledgement."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from discord import app_commands

from main import on_app_command_error


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
