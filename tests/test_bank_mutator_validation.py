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


if __name__ == "__main__":
    unittest.main()
