"""Regression test for the registered prefix profile alias.

The live traceback reported Currency.profile() missing self and ctx. The
class method is correctly declared on this branch, so guard the actual Cog
registration and invocation path instead of changing a valid callback signature.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.casino.currency import Currency


class CurrencyProfileRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
        )
        self.cog = Currency(self.bot)
        await self.bot.add_cog(self.cog)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_profile_alias_invokes_the_registered_cog_callback(self):
        command = self.bot.get_command("profile")
        self.assertIsNotNone(command)
        self.assertIs(command.cog, self.cog)
        self.assertEqual(command.name, "بروفايل")
        self.assertIn("profile", command.aliases)

        author = SimpleNamespace(id=101, display_name="Test member")
        message = SimpleNamespace(
            id=404,
            author=author,
            guild=None,
            channel=SimpleNamespace(id=303),
            content="!profile",
            attachments=[],
            _state=self.bot._connection,
        )
        context = commands.Context(
            message=message,
            bot=self.bot,
            view=StringView(""),
            prefix="!",
            command=command,
            invoked_with="profile",
        )

        with (
            patch("discord.ext.commands.context.Context.send", new=AsyncMock()) as send,
            patch("cogs.casino.currency.bank.get_balance", return_value=100),
            patch("cogs.casino.currency.bank.get_daily", return_value=(0, 1)),
            patch("cogs.casino.currency.bank.get_inventory", return_value={}),
        ):
            await command.invoke(context)

        send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
