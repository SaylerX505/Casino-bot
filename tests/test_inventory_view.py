"""Regression tests for the interactive inventory view."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import discord

from cogs.casino.currency import Currency, CurrencyHubLayout, InventoryView, setup


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


class InventoryViewTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_use_buttons_reflect_inventory_counts_after_off_loop_snapshot_refresh(self):
        self.assertFalse(self.button("inventory_use_box").disabled)
        self.assertTrue(self.button("inventory_use_ticket").disabled)

        self.cog.inventory["loot_box"] = 0
        self.cog.inventory["luck_ticket"] = 1
        await self.view.refresh_snapshot()

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



class PersistentEconomyViewLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_is_registered_on_load_and_removed_on_unload(self):
        bot = SimpleNamespace(add_view=Mock(), remove_view=Mock())
        cog = Currency(bot)

        await cog.cog_load()

        bot.add_view.assert_called_once()
        view = bot.add_view.call_args.args[0]
        self.assertIsInstance(view, CurrencyHubLayout)
        self.assertIsNone(view.timeout)
        self.assertIs(bot._currency_hub_layout, view)

        cog.cog_unload()

        bot.remove_view.assert_called_once_with(view)
        self.assertIsNone(cog._persistent_hub_view)
        self.assertFalse(hasattr(bot, "_currency_hub_layout"))

    async def test_extension_setup_registers_persistent_hub_once(self):
        bot = SimpleNamespace(add_view=Mock(), remove_view=Mock())

        async def add_cog(cog):
            self.assertIsInstance(cog, Currency)
            await cog.cog_load()
            bot.loaded_cog = cog

        bot.add_cog = add_cog
        await setup(bot)

        bot.add_view.assert_called_once()
        bot.remove_view.assert_not_called()
        self.assertIsInstance(bot.loaded_cog._persistent_hub_view, CurrencyHubLayout)
        self.assertIs(bot._currency_hub_layout, bot.loaded_cog._persistent_hub_view)


if __name__ == "__main__":
    unittest.main()
