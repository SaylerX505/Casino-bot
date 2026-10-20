"""Regression tests for grouped slash command registration."""
import unittest

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utility.help import HelpCommand


class CommandGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(
            command_prefix="+",
            intents=discord.Intents.none(),
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


if __name__ == "__main__":
    unittest.main()
