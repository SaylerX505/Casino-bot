"""Regression tests for grouped slash command registration."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import LayoutView

from cogs.utility.admin import AdminCommands
from cogs.utility.help import HelpCommand, HelpEntryView
from main import sync_slash_commands, validate_slash_command_metadata


class CommandGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(
            command_prefix="+",
            intents=discord.Intents.none(),
            help_command=None,
        )
        await self.bot.add_cog(HelpCommand(self.bot))
        await self.bot.add_cog(AdminCommands(self.bot))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_registered_slash_commands_have_visible_descriptions(self):
        problems = validate_slash_command_metadata(self.bot.tree.get_commands())
        self.assertEqual(problems, [])

        command = self.bot.tree.get_command("commands")
        self.assertIsInstance(command, app_commands.Command)
        self.assertEqual(command.description, "فتح قائمة الأوامر والمساعدة")

    async def test_metadata_validator_allows_context_menu_commands(self):
        async def inspect_member(
            interaction: discord.Interaction,
            user: discord.User,
        ):
            return None

        context_menu = app_commands.ContextMenu(
            name="Inspect member",
            callback=inspect_member,
        )
        self.bot.tree.add_command(context_menu)
        problems = validate_slash_command_metadata(self.bot.tree.get_commands())
        self.assertEqual(problems, [])

    async def test_sync_preflight_blocks_invisible_description_and_names_command(self):
        async def broken_command(interaction: discord.Interaction):
            return None

        self.bot.tree.add_command(
            app_commands.Command(
                name="broken",
                description="\u200b",
                callback=broken_command,
            )
        )
        logger = Mock()

        with patch.object(
            self.bot.tree,
            "sync",
            new=AsyncMock(return_value=[]),
        ) as sync:
            synced = await sync_slash_commands(
                self.bot.tree,
                home_guild_id=123456,
                logger=logger,
            )

        self.assertIsNone(synced)
        # Only the global-command cleanup is synced; the invalid guild command
        # batch is rejected locally before making a second API request.
        sync.assert_awaited_once_with()
        logger.error.assert_called_once()
        log_message = " ".join(str(value) for value in logger.error.call_args.args)
        self.assertIn("broken", log_message)
        self.assertIn("invisible", log_message)
        self.assertEqual(self.bot.tree.get_commands(), [])
        guild_commands = self.bot.tree.get_commands(guild=discord.Object(id=123456))
        self.assertIn("broken", {command.name for command in guild_commands})

    async def test_prefix_help_entry_uses_components_v2(self):
        view = HelpEntryView(self.bot)

        self.assertIsInstance(view, LayoutView)
        self.assertEqual(view.timeout, 180)
        texts = [
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        self.assertIn("مركز المساعدة", "\n".join(texts))
        self.assertNotIn("Help Center", "\n".join(texts))
        buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].style, discord.ButtonStyle.secondary)

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

    async def test_admin_group_contains_management_commands(self):
        group = self.bot.tree.get_command("admin")

        self.assertIsInstance(group, app_commands.Group)
        names = {command.name for command in group.commands}
        self.assertTrue(
            {"access", "economy-credit", "economy-debit", "ai-toggle", "bank-reset", "status"}.issubset(names)
        )
        access_group = group.get_command("access")
        self.assertIsInstance(access_group, app_commands.Group)
        self.assertTrue(
            {"add", "remove", "list"}.issubset({command.name for command in access_group.commands})
        )
        self.assertIsNone(self.bot.tree.get_command("owner"))

    async def test_public_help_does_not_include_owner_commands(self):
        from cogs.utility.help import HELP_PAGES, SLASH_COMMANDS

        self.assertNotIn("owner", HELP_PAGES)
        self.assertNotIn("bankreset", SLASH_COMMANDS)
        self.assertNotIn("ai", SLASH_COMMANDS)
    async def test_prefix_stop_errors_do_not_pass_ephemeral_argument(self):
        fake_bot = SimpleNamespace(active_games={})
        cog = HelpCommand(fake_bot)
        context = SimpleNamespace(
            bot=fake_bot,
            channel=SimpleNamespace(id=9001),
            author=SimpleNamespace(id=101),
            interaction=None,
            send=AsyncMock(),
        )

        await HelpCommand.stop_game.callback(cog, context)

        context.send.assert_awaited_once()
        self.assertNotIn("ephemeral", context.send.await_args.kwargs)
        self.assertIsInstance(context.send.await_args.kwargs["view"], LayoutView)

    async def test_slash_stop_errors_remain_ephemeral(self):
        fake_bot = SimpleNamespace(active_games={})
        cog = HelpCommand(fake_bot)
        context = SimpleNamespace(
            bot=fake_bot,
            channel=SimpleNamespace(id=9002),
            author=SimpleNamespace(id=101),
            interaction=object(),
            send=AsyncMock(),
        )

        await HelpCommand.stop_game.callback(cog, context)

        context.send.assert_awaited_once()
        self.assertTrue(context.send.await_args.kwargs["ephemeral"])
        self.assertIsInstance(context.send.await_args.kwargs["view"], LayoutView)

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
