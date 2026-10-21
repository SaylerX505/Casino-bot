"""Tests that prefix and slash commands obey the configured home guild."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


class HomeGuildPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_commands_are_rejected_outside_configured_guild(self):
        ctx = SimpleNamespace(guild=SimpleNamespace(id=101))
        with patch("main.config.HOME_GUILD_ID", 202):
            self.assertFalse(await main.enforce_home_guild_for_prefix(ctx))
            self.assertTrue(await main.enforce_home_guild_for_prefix(SimpleNamespace(guild=SimpleNamespace(id=202))))

    async def test_startup_refuses_to_run_without_home_guild(self):
        with (
            patch("main.config.TOKEN", "test-token"),
            patch("main.config.HOME_GUILD_ID", None),
            patch("main.logging.getLogger") as get_logger,
        ):
            await main.main()

        get_logger.return_value.error.assert_called_once()
        self.assertIn("HOME_GUILD_ID is required", get_logger.return_value.error.call_args.args[0])

    async def test_slash_commands_acknowledge_wrong_guild_privately(self):
        interaction = SimpleNamespace(
            guild_id=101,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        )
        with patch("main.config.HOME_GUILD_ID", 202):
            allowed = await main.bot.tree.interaction_check(interaction)

        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_slash_commands_allow_home_guild(self):
        interaction = SimpleNamespace(guild_id=202, response=SimpleNamespace(send_message=AsyncMock()))
        with patch("main.config.HOME_GUILD_ID", 202):
            self.assertTrue(await main.bot.tree.interaction_check(interaction))
        interaction.response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
