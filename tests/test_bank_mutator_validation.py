"""Input validation tests for the economy's shared mutation API."""
import unittest
from unittest.mock import patch

from cogs.casino import bank


class BankMutatorValidationTests(unittest.TestCase):
    def test_balance_rejects_negative_and_non_sqlite_range_values(self):
        with self.assertRaises(ValueError):
            bank.set_balance(101, -1)
        with self.assertRaises(ValueError):
            bank.set_balance(101, (1 << 63))
        with self.assertRaises(TypeError):
            bank.set_balance(101, True)

    def test_add_item_requires_a_positive_integer_quantity(self):
        with self.assertRaises(ValueError):
            bank.add_item(101, "mafia_scan", 0)
        with self.assertRaises(ValueError):
            bank.add_item(101, "mafia_scan", -1)
        with self.assertRaises(ValueError):
            bank.add_item(101, "mafia_scan", True)

    def test_remove_item_invalid_quantity_fails_without_touching_storage(self):
        with patch("cogs.casino.bank._connect", side_effect=AssertionError("database touched")) as connect:
            self.assertFalse(bank.remove_item(101, "mafia_scan", 0))
            self.assertFalse(bank.remove_item(101, "mafia_scan", -1))
            self.assertFalse(bank.remove_item(101, "mafia_scan", True))
        connect.assert_not_called()

    def test_balance_mutators_reject_non_integer_amounts(self):
        invalid_amounts = (True, 1.5, "10")
        for amount in invalid_amounts:
            with self.subTest(amount=amount):
                with patch("cogs.casino.bank.get_balance", return_value=123):
                    self.assertEqual(bank.debit_balance(101, amount), (False, 123))
                    self.assertEqual(bank.credit_balance(101, amount), (False, 123))
                    self.assertEqual(bank.transfer_balance(101, 202, amount), (False, 123))
                    self.assertEqual(bank.charge_up_to(101, amount), (0, 123))
                with self.assertRaises(TypeError):
                    bank.update_balance(101, amount)

                with patch(
                    "cogs.casino.bank._connect",
                    side_effect=AssertionError("invalid wager amount must not access storage"),
                ) as connect:
                    self.assertFalse(bank.create_rps_wager("invalid-amount", 101, 202, amount))
                connect.assert_not_called()

    def test_daily_state_rejects_invalid_timestamps_and_streaks(self):
        for invalid_timestamp in (-1, float("nan"), float("inf"), "not-a-time"):
            with (
                self.subTest(timestamp=invalid_timestamp),
                self.assertRaises(ValueError),
            ):
                bank.set_daily(101, invalid_timestamp, 0)

        with self.assertRaises(TypeError):
            bank.set_daily(101, True, 0)

        for invalid_streak in (-1, True, 1.5):
            with (
                self.subTest(streak=invalid_streak),
                self.assertRaises(ValueError),
            ):
                bank.set_daily(101, 0, invalid_streak)



    def test_purchase_rejects_implicit_integer_coercion_before_storage_access(self):
        for quantity in (True, 1.5, "2"):
            with (
                self.subTest(quantity=quantity),
                patch("cogs.casino.bank.get_balance", return_value=123),
                patch("cogs.casino.bank._connect", side_effect=AssertionError("invalid purchase touched storage")) as connect,
            ):
                self.assertEqual(bank.purchase_item(101, "shield", quantity, 5), (False, 123))
                connect.assert_not_called()

        for unit_price in (True, 1.5, "5"):
            with (
                self.subTest(unit_price=unit_price),
                patch("cogs.casino.bank.get_balance", return_value=123),
                patch("cogs.casino.bank._connect", side_effect=AssertionError("invalid purchase touched storage")) as connect,
            ):
                self.assertEqual(bank.purchase_item(101, "shield", 1, unit_price), (False, 123))
                connect.assert_not_called()

    def test_daily_claim_rejects_invalid_timestamp_and_reward_types(self):
        for timestamp, expected_error in (
            (True, TypeError),
            ("2000000000", TypeError),
            (-1, ValueError),
            (float("nan"), ValueError),
            (float("inf"), ValueError),
        ):
            with (
                self.subTest(timestamp=timestamp),
                patch("cogs.casino.bank._connect", side_effect=AssertionError("invalid daily input touched storage")),
                self.assertRaises(expected_error),
            ):
                bank.claim_daily(101, timestamp, 20)

        for reward in (True, 1.5, "20", -1, bank.SQLITE_MAX_INT + 1):
            expected_error = TypeError if isinstance(reward, (bool, float, str)) else ValueError
            with (
                self.subTest(reward=reward),
                patch("cogs.casino.bank._connect", side_effect=AssertionError("invalid daily input touched storage")),
                self.assertRaises(expected_error),
            ):
                bank.claim_daily(101, 2_000_000_000.0, reward)

    def test_redemption_rejects_implicit_reward_coercion_before_storage_access(self):
        for reward in (True, 1.5, "10", -1, bank.SQLITE_MAX_INT + 1):
            with (
                self.subTest(reward=reward),
                patch("cogs.casino.bank.get_balance", return_value=123),
                patch(
                    "cogs.casino.bank._connect",
                    side_effect=AssertionError("invalid redemption touched storage"),
                ) as connect,
            ):
                self.assertEqual(bank.redeem_item(101, "loot_box", reward), (False, 123))
                connect.assert_not_called()

    def test_simple_pvp_bet_rejects_implicit_integer_coercion(self):
        for amount in (True, 1.5, "10", 0, -1, bank.SQLITE_MAX_INT + 1):
            with self.subTest(amount=amount), patch(
                "cogs.casino.bank._connect",
                side_effect=AssertionError("invalid wager touched storage"),
            ) as connect:
                self.assertFalse(bank.place_bet(101, 202, amount))
                connect.assert_not_called()

    def test_wheel_settlement_rejects_coerced_amounts_before_storage_access(self):
        invalid_cases = (
            ((101, True, 0), {}),
            ((101, 50, 1.5), {}),
            ((101, 50, 0), {"target_loss": True}),
            ((101, 50, 0), {"target_loss": "10"}),
        )
        for args, kwargs in invalid_cases:
            with (
                self.subTest(args=args, kwargs=kwargs),
                patch(
                    "cogs.casino.bank._connect",
                    side_effect=AssertionError("invalid wheel input touched storage"),
                ),
                self.assertRaises(TypeError),
            ):
                bank.settle_wheel(*args, **kwargs)

if __name__ == "__main__":
    unittest.main()
