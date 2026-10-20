"""Regression tests for the interactive inventory view."""
import unittest
from types import SimpleNamespace

import discord

from cogs.casino.currency import CurrencyHubLayout, InventoryView


class FakeCurrency:
    def __init__(self):
        self.inventory = {
            "double_shot": 2,
            "loot_box": 1,
            "luck_ticket": 0,
        }
        self.balance = 175

    def get_inventory(self, user_id):
        return dict(self.inventory)

    def get_balance(self, user_id):
        return self.balance

    def get_item_count(self, user_id, item_id):
        return self.inventory.get(item_id, 0)

    def check_whitelist(self, user):
        return False


class InventoryViewTests(unittest.TestCase):
    def setUp(self):
        self.owner = SimpleNamespace(
            id=123,
            display_name="Test Player",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        self.cog = FakeCurrency()
        self.view = InventoryView(self.cog, self.owner)

    def button(self, custom_id):
        return next(
            child
            for child in self.view.walk_children()
            if getattr(child, "custom_id", None) == custom_id
        )

    def test_shows_owned_items_purpose_price_and_balance(self):
        embed = self.view.make_embed()

        self.assertIn("طلقة مزدوجة", embed.description)
        self.assertIn("السعر في المتجر", embed.description)
        self.assertIn("صندوق حظ", embed.description)
        self.assertIn("175", embed.description)

    def test_category_filter_separates_tools_from_consumables(self):
        self.view.category = "consumables"
        consumables = self.view.make_embed().description
        self.assertIn("صندوق حظ", consumables)
        self.assertNotIn("طلقة مزدوجة", consumables)

        self.view.category = "tools"
        tools = self.view.make_embed().description
        self.assertIn("طلقة مزدوجة", tools)
        self.assertNotIn("صندوق حظ", tools)

    def test_use_buttons_reflect_inventory_counts(self):
        self.assertFalse(self.button("inventory_use_box").disabled)
        self.assertTrue(self.button("inventory_use_ticket").disabled)

        self.cog.inventory["loot_box"] = 0
        self.cog.inventory["luck_ticket"] = 1
        self.view.refresh_buttons()

        self.assertTrue(self.button("inventory_use_box").disabled)
        self.assertFalse(self.button("inventory_use_ticket").disabled)

    def test_v2_economy_hub_uses_persistent_custom_ids(self):
        view = CurrencyHubLayout(self.cog)
        buttons = [
            child
            for child in view.walk_children()
            if isinstance(child, discord.ui.Button)
        ]
        custom_ids = {button.custom_id for button in buttons}

        self.assertIsNone(view.timeout)
        self.assertTrue(all(custom_ids))
        self.assertTrue({"hubv2_daily", "hubv2_top", "hubv2_shop", "hubv2_transfer"}.issubset(custom_ids))


if __name__ == "__main__":
    unittest.main()
