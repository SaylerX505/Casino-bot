"""Regression tests for balanced, predictable economy rewards."""

import unittest

from cogs.casino.currency_catalog import (
    CONSUMABLE_REWARDS,
    DAILY_REWARD_MAX,
    DAILY_REWARD_MIN,
    INVENTORY_ITEMS,
)


class EconomyBalanceTests(unittest.TestCase):
    def test_daily_income_is_meaningful_without_becoming_inflationary(self):
        self.assertEqual((DAILY_REWARD_MIN, DAILY_REWARD_MAX), (40, 60))
        self.assertLessEqual(DAILY_REWARD_MAX + 7 * 10, 130)

    def test_consumable_expected_values_are_below_shop_prices(self):
        prices = {item[0]: item[3] for item in INVENTORY_ITEMS}
        for item_id, rewards in CONSUMABLE_REWARDS.items():
            with self.subTest(item_id=item_id):
                self.assertTrue(rewards)
                self.assertGreater(min(rewards), 0)
                expected_reward = sum(rewards) / len(rewards)
                self.assertGreater(expected_reward, prices[item_id] * 0.7)
                self.assertLess(expected_reward, prices[item_id])

    def test_consumable_reward_tables_keep_known_upper_bounds(self):
        self.assertEqual(max(CONSUMABLE_REWARDS["luck_ticket"]), 140)
        self.assertEqual(max(CONSUMABLE_REWARDS["loot_box"]), 400)


if __name__ == "__main__":
    unittest.main()
