"""Tests for the private interactive setup wizard."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord
from discord.ui import LayoutView

from cogs.casino.permissions import GAME_LABELS
from cogs.utility.wizard import (
    AdminRoleWizardView,
    GameChannelWizardView,
    SetupWizard,
    StaffRoleWizardView,
    WizardHomeView,
    WizardReviewView,
)


class WizardViewTests(unittest.TestCase):
    def setUp(self):
        self.guild = SimpleNamespace(id=123456, owner_id=101)
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

    def test_channel_wizard_has_every_game_and_channel_selector(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = GameChannelWizardView(self.guild, self.owner, selected_game="roulette")

        self.assertIsInstance(view, LayoutView)
        selects = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual(len(selects[0].options), len(GAME_LABELS))
        self.assertTrue(any(isinstance(item, discord.ui.ChannelSelect) for item in view.walk_children()))
        self.assertEqual(view.selected_game, "roulette")
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertIn("wizard_channels_all_roulette", {button.custom_id for button in buttons})

    def test_review_lists_every_game_and_roles(self):
        with patch("cogs.utility.wizard.bank.get_setting", side_effect=self._get_setting):
            view = WizardReviewView(self.guild, self.owner)
        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        for game_name in GAME_LABELS.values():
            self.assertIn(game_name, content)
        self.assertIn("<@&300>", content)

    def test_wizard_is_a_guild_slash_command(self):
        self.assertEqual(SetupWizard.wizard.name, "wizard")


if __name__ == "__main__":
    unittest.main()
