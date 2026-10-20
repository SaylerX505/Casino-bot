"""Regression tests for grouped slash command registration."""
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utility.help import HelpCommand


class CommandGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(
            command_prefix="+",
            intents=discord.Intents.none(),
            help_command=None,
        )
        await self.bot.add_cog(HelpCommand(self.bot))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_games_group_exposes_existing_game_commands(self):
        group = self.bot.tree.get_command("games")

        self.assertIsInstance(group, app_commands.Group)
        names = {command.name for command in group.commands}
        self.assertTrue(
            {"mafia", "roulette", "hide-and-seek", "geo", "chairs", "wheel", "riddle"}.issubset(names)
        )

    async def test_economy_group_exposes_personal_economy_tools(self):
        group = self.bot.tree.get_command("economy")

        self.assertIsInstance(group, app_commands.Group)
        names = {command.name for command in group.commands}
        self.assertTrue(
            {"balance", "inventory", "profile", "daily", "top", "shop"}.issubset(names)
        )

    async def test_prefix_compatibility_is_preserved(self):
        self.assertIsNotNone(self.bot.get_command("help"))
        self.assertIsNotNone(self.bot.get_command("stop"))

    async def test_group_dispatch_reuses_existing_hybrid_command(self):
        command = Mock()
        command.invoke = AsyncMock()
        command.dispatch_error = AsyncMock()
        fake_bot = Mock()
        fake_bot.get_command.return_value = command
        cog = HelpCommand(fake_bot)
        context = object()
        interaction = object()

        with patch.object(
            commands.Context,
            "from_interaction",
            new=AsyncMock(return_value=context),
        ):
            await cog._invoke_hybrid_command(interaction, "مافيا")

        command.invoke.assert_awaited_once_with(context)
        command.dispatch_error.assert_not_awaited()

    async def test_group_dispatch_routes_command_errors(self):
        command = Mock()
        command.invoke = AsyncMock(side_effect=commands.CheckFailure())
        command.dispatch_error = AsyncMock()
        fake_bot = Mock()
        fake_bot.get_command.return_value = command
        cog = HelpCommand(fake_bot)
        context = object()
        interaction = object()

        with patch.object(
            commands.Context,
            "from_interaction",
            new=AsyncMock(return_value=context),
        ):
            await cog._invoke_hybrid_command(interaction, "مافيا")

        command.dispatch_error.assert_awaited_once()

    async def test_unknown_group_target_returns_private_error(self):
        fake_bot = Mock()
        fake_bot.get_command.return_value = None
        cog = HelpCommand(fake_bot)
        interaction = Mock()
        interaction.response.send_message = AsyncMock()

        await cog._invoke_hybrid_command(interaction, "missing-command")

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

if __name__ == "__main__":
    unittest.main()
