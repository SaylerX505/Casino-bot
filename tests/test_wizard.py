"""Tests for the private interactive setup wizard."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ui import LayoutView

from cogs.utility.wizard import (
    AdminRoleWizardView,
    AISettingsWizardView,
    GameChannelWizardView,
    LogChannelWizardView,
    SetupWizard,
    StaffRoleWizardView,
    WizardHomeView,
    WizardLayout,
    WizardReviewView,
)


class WizardViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guild = SimpleNamespace(id=123456, owner_id=101, me=object())
        self.owner = SimpleNamespace(id=101)

    def _get_setting(self, key, default=None):
        if ":game_channels:" in key:
            return default or "all"
        if key.endswith(":staff_role_ids"):
            return "[200, 201]"
        if key.endswith(":manager_role_ids"):
            return "[300]"
        return default

    def test_home_view_summarizes_admin_roles_staff_roles_and_channel_policy(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = WizardHomeView(self.guild, self.owner)

        self.assertIsInstance(view, LayoutView)
        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("رولات إدارة البوت", content)
        self.assertIn("رولات الستاف", content)
        self.assertIn("<@&300>", content)
        self.assertIn("<@&200>", content)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertGreaterEqual(len(buttons), 5)
        self.assertIn("wizard_manager_roles", {button.custom_id for button in buttons})
        self.assertIn("wizard_game_channels", {button.custom_id for button in buttons})
        self.assertIn("wizard_ai_settings", {button.custom_id for button in buttons})

    def test_staff_role_page_contains_role_picker_and_safe_clear_action(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = StaffRoleWizardView(self.guild, self.owner)

        self.assertIsInstance(view, LayoutView)
        self.assertTrue(any(isinstance(item, discord.ui.RoleSelect) for item in view.walk_children()))
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertIn("wizard_clear_staff_roles", {button.custom_id for button in buttons})

    def test_manager_role_page_is_separate_from_staff_roles(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = AdminRoleWizardView(self.guild, self.owner)

        self.assertIsInstance(view, LayoutView)
        self.assertTrue(any(isinstance(item, discord.ui.RoleSelect) for item in view.walk_children()))
        text = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("رولات إدارة البوت", text)
        self.assertIn("Administrator", text)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertIn("wizard_clear_manager_roles", {button.custom_id for button in buttons})

    def test_channel_wizard_has_one_global_channel_selector(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = GameChannelWizardView(self.guild, self.owner)

        self.assertIsInstance(view, LayoutView)
        channel_selects = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.ChannelSelect)
        ]
        self.assertEqual(len(channel_selects), 1)
        self.assertEqual(channel_selects[0].max_values, 1)
        self.assertFalse(
            any(isinstance(item, discord.ui.Select) for item in view.walk_children())
        )
        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("كل الألعاب", content)
        self.assertIn("قناة الألعاب العامة", content)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        button_ids = {button.custom_id for button in buttons}
        self.assertIn("wizard_channels_all", button_ids)
        self.assertIn("wizard_channels_back", button_ids)
        self.assertNotIn("wizard_channels_disable_roulette", button_ids)

    def test_review_lists_one_global_game_channel_and_roles(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = WizardReviewView(self.guild, self.owner)
        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("جميع الألعاب", content)
        self.assertIn("كل القنوات", content)
        self.assertIn("<@&300>", content)

    def test_ai_settings_page_exposes_toggle_and_role_allowlist(self):
        with (
            patch("cogs.utility.wizard._global_ai_enabled", return_value=True),
            patch("cogs.utility.wizard._guild_ai_enabled", return_value=True),
            patch("cogs.utility.wizard.configured_ai_role_ids", return_value={400}),
        ):
            view = AISettingsWizardView(self.guild, self.owner)

        self.assertIsInstance(view, LayoutView)
        self.assertTrue(
            any(isinstance(item, discord.ui.RoleSelect) for item in view.walk_children())
        )
        content = "\\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("مفتاح API", content)
        self.assertIn("<@&400>", content)
        button_ids = {
            item.custom_id
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        }
        self.assertIn("wizard_ai_toggle", button_ids)
        self.assertIn("wizard_ai_clear_roles", button_ids)
        self.assertIn("wizard_ai_back", button_ids)

    async def test_ai_toggle_persists_a_guild_override_not_the_global_master_switch(self):
        interaction = SimpleNamespace(
            user=self.owner,
            guild=self.guild,
            guild_id=self.guild.id,
            message=SimpleNamespace(id=555),
            client=SimpleNamespace(get_cog=lambda _name: object()),
            response=SimpleNamespace(
                edit_message=AsyncMock(),
                send_message=AsyncMock(),
            ),
        )
        with (
            patch("cogs.utility.wizard._guard_interaction", return_value=True),
            patch("cogs.utility.wizard._global_ai_enabled", return_value=True),
            patch("cogs.utility.wizard._guild_ai_enabled", return_value=False),
            patch("cogs.utility.wizard.config.DEEPSEEK_API_KEY", "test-key"),
            patch("cogs.utility.wizard.bank.set_setting") as save_setting,
            patch("cogs.utility.wizard.configured_ai_role_ids", return_value=set()),
        ):
            view = AISettingsWizardView(self.guild, self.owner)
            await view.toggle_ai(interaction)

        save_setting.assert_called_once_with(
            f"guild:{self.guild.id}:ai_enabled",
            "true",
        )
        interaction.response.edit_message.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    async def test_global_channel_save_persists_one_channel_for_all_games(self):
        channel = SimpleNamespace(
            id=555,
            guild=self.guild,
            permissions_for=lambda _member: SimpleNamespace(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        )
        interaction = SimpleNamespace(
            user=self.owner,
            guild=self.guild,
            guild_id=self.guild.id,
            message=SimpleNamespace(id=777),
            response=SimpleNamespace(
                edit_message=AsyncMock(),
                send_message=AsyncMock(),
            ),
        )
        with (
            patch("cogs.utility.wizard._guard_interaction", return_value=True),
            patch("cogs.utility.wizard.bank.get_setting", return_value="all"),
            patch("cogs.utility.wizard.bank.set_setting") as save_setting,
        ):
            view = GameChannelWizardView(self.guild, self.owner)
            view.channel_select = SimpleNamespace(values=[channel])
            await view.save_channel(interaction)

        save_setting.assert_called_once_with(
            "guild:123456:games_channel",
            "555",
        )
        interaction.response.edit_message.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    async def test_global_channel_save_rejects_missing_game_permissions(self):
        channel = SimpleNamespace(
            id=556,
            guild=self.guild,
            permissions_for=lambda _member: SimpleNamespace(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=False,
            ),
        )
        interaction = SimpleNamespace(
            user=self.owner,
            guild=self.guild,
            guild_id=self.guild.id,
            message=SimpleNamespace(id=779),
            response=SimpleNamespace(
                edit_message=AsyncMock(),
                send_message=AsyncMock(),
            ),
        )
        with (
            patch("cogs.utility.wizard._guard_interaction", return_value=True),
            patch("cogs.utility.wizard.bank.get_setting", return_value="all"),
            patch("cogs.utility.wizard.bank.set_setting") as save_setting,
        ):
            view = GameChannelWizardView(self.guild, self.owner)
            view.channel_select = SimpleNamespace(values=[channel])
            await view.save_channel(interaction)

        save_setting.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        self.assertIn(
            "رفع الملفات",
            interaction.response.send_message.await_args.args[0],
        )
        interaction.response.edit_message.assert_not_awaited()

    async def test_global_channel_clear_restores_all_channels(self):
        interaction = SimpleNamespace(
            user=self.owner,
            guild=self.guild,
            guild_id=self.guild.id,
            message=SimpleNamespace(id=778),
            response=SimpleNamespace(
                edit_message=AsyncMock(),
                send_message=AsyncMock(),
            ),
        )
        with (
            patch("cogs.utility.wizard._guard_interaction", return_value=True),
            patch("cogs.utility.wizard.bank.get_setting", return_value="555"),
            patch("cogs.utility.wizard.bank.set_setting") as save_setting,
        ):
            view = GameChannelWizardView(self.guild, self.owner)
            await view.allow_all_channels(interaction)

        save_setting.assert_called_once_with(
            "guild:123456:games_channel",
            "all",
        )
        interaction.response.edit_message.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    def test_log_wizard_has_per_type_channel_select_and_disable_action(self):
        with patch("cogs.utility.wizard._configured_log_channel", return_value=0):
            view = LogChannelWizardView(self.guild, self.owner, selected_type="economy")

        self.assertIsInstance(view, LayoutView)
        selects = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual({option.value for option in selects[0].options}, {"economy", "command_errors"})
        self.assertTrue(
            any(isinstance(item, discord.ui.ChannelSelect) for item in view.walk_children())
        )
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        ids = {button.custom_id for button in buttons}
        self.assertIn("wizard_logs_disable", ids)
        self.assertIn("wizard_logs_back", ids)

    def test_review_includes_configured_log_destinations(self):
        with (
            patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting),
            patch(
                "cogs.utility.wizard._configured_log_channel",
                side_effect=lambda _guild_id, log_type: 900 if log_type == "economy" else 901,
            ),
        ):
            view = WizardReviewView(self.guild, self.owner)

        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("<#900>", content)
        self.assertIn("<#901>", content)
        self.assertIn("قنوات السجلات", content)

    async def test_transition_binds_the_new_view_to_the_original_message(self):
        message = SimpleNamespace(id=456)
        response = SimpleNamespace(edit_message=AsyncMock())
        interaction = SimpleNamespace(message=message, response=response)
        source_view = WizardLayout(self.guild, self.owner)
        target_view = WizardLayout(self.guild, self.owner)

        await source_view._edit_message(interaction, view=target_view)

        self.assertIs(target_view.message, message)
        response.edit_message.assert_awaited_once_with(view=target_view)

    def test_wizard_is_a_guild_slash_command(self):
        self.assertEqual(SetupWizard.wizard.name, "wizard")


if __name__ == "__main__":
    unittest.main()
