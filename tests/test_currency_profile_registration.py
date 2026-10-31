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

from cogs.casino.currency import Currency, TransferConfirmationView


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

    def make_context(self, command, *, content, invoked_with, author=None, guild=None):
        author = author or SimpleNamespace(id=101, display_name="Test member", bot=False)
        message = SimpleNamespace(
            id=404,
            author=author,
            guild=guild,
            channel=SimpleNamespace(id=303, send=AsyncMock()),
            content=content,
            attachments=[],
            _state=self.bot._connection,
        )
        return commands.Context(
            message=message,
            bot=self.bot,
            view=StringView(""),
            prefix="!",
            command=command,
            invoked_with=invoked_with,
        )

    async def test_balance_command_has_no_economy_buttons(self):
        command = self.bot.get_command("bal")
        context = self.make_context(command, content="!bal", invoked_with="bal")
        with (
            patch("discord.ext.commands.context.Context.send", new=AsyncMock()) as send,
            patch("cogs.casino.currency.bank.get_balance", return_value=275),
        ):
            await command.invoke(context)

        send.assert_awaited_once()
        view = send.await_args.kwargs["view"]
        text = "\\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("275", text)
        self.assertFalse(any(isinstance(item, discord.ui.Button) for item in view.walk_children()))

    async def test_daily_prefix_command_is_registered(self):
        command = self.bot.get_command("daily")
        self.assertIsNotNone(command)
        self.assertIn("يومية", command.aliases)
        context = self.make_context(command, content="!daily", invoked_with="daily")
        with (
            patch("discord.ext.commands.context.Context.send", new=AsyncMock()) as send,
            patch(
                "cogs.casino.currency.bank.claim_daily",
                return_value={"claimed": True, "amount": 30, "streak": 2, "balance": 100},
            ),
        ):
            await command.invoke(context)

        send.assert_awaited_once()
        self.assertIn("30", "\\n".join(
            item.content for item in send.await_args.kwargs["view"].walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ))

    async def test_transfer_alias_opens_confirmation_without_moving_coins(self):
        command = self.bot.get_command("ckrc")
        self.assertIsNotNone(command)
        self.assertIs(command, self.bot.get_command("تحويل"))
        sender = SimpleNamespace(id=101, display_name="Sender", bot=False)
        recipient = SimpleNamespace(id=202, display_name="Recipient", bot=False, mention="<@202>")
        guild = SimpleNamespace(id=999)
        context = self.make_context(
            command, content="!ckrc <@202> 40", invoked_with="ckrc", author=sender, guild=guild
        )
        with (
            patch("discord.ext.commands.context.Context.send", new=AsyncMock()) as send,
            patch("cogs.casino.currency.bank.get_balance", return_value=100),
            patch("cogs.casino.currency.bank.transfer_balance") as transfer,
        ):
            await command.callback(self.cog, context, recipient, 40)

        send.assert_awaited_once()
        self.assertIsInstance(send.await_args.kwargs["view"], TransferConfirmationView)
        transfer.assert_not_called()

    async def test_transfer_confirmation_is_single_use_and_updates_same_panel(self):
        sender = SimpleNamespace(id=101, display_name="Sender", bot=False)
        recipient = SimpleNamespace(id=202, display_name="Recipient", bot=False, mention="<@202>")
        view = TransferConfirmationView(self.cog, sender, recipient, 40)
        guild = SimpleNamespace(id=999)
        interaction = SimpleNamespace(
            user=sender,
            guild=guild,
            message=SimpleNamespace(edit=AsyncMock()),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
        )
        with (
            patch("cogs.casino.currency.bank.transfer_balance", return_value=(True, 60)) as transfer,
            patch.object(self.cog, "log_action", new=AsyncMock()),
        ):
            await view.confirm_transfer(interaction)

        transfer.assert_called_once_with(101, 202, 40)
        interaction.message.edit.assert_awaited_once_with(view=view)
        self.assertTrue(view.done)
        self.assertTrue(view.confirm_button.disabled)
        self.assertTrue(view.cancel_button.disabled)
        text = "\\n".join(
            item.content for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("تم التحويل بنجاح", text)
        self.assertIn("60", text)

    async def test_cancelled_transfer_never_calls_bank(self):
        sender = SimpleNamespace(id=101, display_name="Sender", bot=False)
        recipient = SimpleNamespace(id=202, display_name="Recipient", bot=False, mention="<@202>")
        view = TransferConfirmationView(self.cog, sender, recipient, 40)
        interaction = SimpleNamespace(
            user=sender,
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
        )
        with patch("cogs.casino.currency.bank.transfer_balance") as transfer:
            await view.cancel_transfer(interaction)

        transfer.assert_not_called()
        interaction.response.edit_message.assert_awaited_once_with(view=view)
        self.assertTrue(view.done)
        self.assertIn("تم إلغاء التحويل", view._panel_text.content)

    async def test_plus_mention_is_not_registered_as_a_profile_command(self):
        listener_names = {name for name, _listener in self.cog.get_listeners()}
        self.assertNotIn("on_message", listener_names)


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
