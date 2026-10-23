"""Tests that prefix and slash commands obey the configured home guild."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import main


def view_text(view):
    return "\\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


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
            patch("main.configure_file_logging"),
            patch("main.install_asyncio_exception_handler"),
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

    async def test_slash_game_command_is_rejected_outside_its_allowed_channels(self):
        interaction = SimpleNamespace(
            guild_id=202,
            channel_id=999,
            command=SimpleNamespace(name="games", qualified_name="games mafia"),
            data={"name": "games", "options": [{"name": "mafia", "type": 1}]},
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        )
        with (
            patch("main.config.HOME_GUILD_ID", 202),
            patch("main.is_game_channel_allowed", return_value=False),
        ):
            allowed = await main.bot.tree.interaction_check(interaction)

        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        panel = interaction.response.send_message.await_args.kwargs["view"]
        self.assertIn("المافيا", view_text(panel))

    async def test_prefix_game_command_is_rejected_outside_its_allowed_channels(self):
        ctx = SimpleNamespace(
            command=SimpleNamespace(name="مافيا"),
            guild=SimpleNamespace(id=202),
            channel=SimpleNamespace(id=999),
        )
        with (
            patch("main.is_game_channel_allowed", return_value=False),
            self.assertRaises(main.GameChannelBlocked),
        ):
            await main.enforce_game_channel_for_prefix(ctx)

    async def test_prefix_game_command_is_allowed_in_configured_channel(self):
        ctx = SimpleNamespace(
            command=SimpleNamespace(name="مافيا"),
            guild=SimpleNamespace(id=202),
            channel=SimpleNamespace(id=200),
        )
        with patch("main.is_game_channel_allowed", return_value=True):
            self.assertTrue(await main.enforce_game_channel_for_prefix(ctx))


if __name__ == "__main__":
    unittest.main()
