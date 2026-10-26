"""Regression tests for the interactive inventory view."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from discord.ext import commands

import discord

from cogs.casino.currency import Currency, CurrencyHubLayout, setup
from cogs.casino.currency_views import InventoryView


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
        self.view = InventoryView(
            self.cog,
            self.owner,
            inventory=self.cog.get_inventory(self.owner.id),
            balance=self.cog.get_balance(self.owner.id),
        )

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
        bot = commands.Bot(command_prefix="+", intents=discord.Intents.none())
        try:
            cog = Currency(bot)
            await cog.cog_load()

            view = cog._persistent_hub_view
            self.assertIsInstance(view, CurrencyHubLayout)
            self.assertIsNone(view.timeout)
            self.assertIs(bot._currency_hub_layout, view)
            self.assertIn(view, bot.persistent_views)

            cog.cog_unload()

            self.assertTrue(view.is_finished())
            self.assertNotIn(view, bot.persistent_views)
            self.assertIsNone(cog._persistent_hub_view)
            self.assertFalse(hasattr(bot, "_currency_hub_layout"))
        finally:
            await bot.close()

    async def test_extension_reload_replaces_the_old_persistent_view_once(self):
        bot = commands.Bot(command_prefix="+", intents=discord.Intents.none())
        try:
            first = Currency(bot)
            await first.cog_load()
            old_view = first._persistent_hub_view
            self.assertIn(old_view, bot.persistent_views)

            first.cog_unload()
            second = Currency(bot)
            await second.cog_load()
            new_view = second._persistent_hub_view

            self.assertIsNot(old_view, new_view)
            self.assertTrue(old_view.is_finished())
            self.assertNotIn(old_view, bot.persistent_views)
            self.assertIn(new_view, bot.persistent_views)
            self.assertIs(bot._currency_hub_layout, new_view)
        finally:
            await bot.close()

    async def test_extension_setup_registers_persistent_hub_once(self):
        bot = commands.Bot(command_prefix="+", intents=discord.Intents.none())
        try:
            await setup(bot)

            cog = bot.get_cog("Currency")
            self.assertIsInstance(cog, Currency)
            self.assertIsInstance(cog._persistent_hub_view, CurrencyHubLayout)
            self.assertIs(bot._currency_hub_layout, cog._persistent_hub_view)
            self.assertEqual(
                sum(view is cog._persistent_hub_view for view in bot.persistent_views),
                1,
            )
        finally:
            await bot.close()


if __name__ == "__main__":
    unittest.main()
