"""Domain validation tests for economy and inventory writes."""
import os
import tempfile
import unittest

from cogs.casino import bank


class BankValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_file = bank.DB_FILE
        self.previous_connection = bank._conn
        bank.DB_FILE = os.path.join(self.temp_dir.name, "bank.db")
        bank._conn = None

    def tearDown(self):
        connection = bank._conn
        if connection is not None:
            connection.close()
        bank._conn = self.previous_connection
        bank.DB_FILE = self.previous_db_file
        self.temp_dir.cleanup()

    def test_set_balance_rejects_negative_and_out_of_range_values(self):
        with self.assertRaises(ValueError):
            bank.set_balance(1, -1)
        with self.assertRaises(ValueError):
            bank.set_balance(1, bank.SQLITE_MAX_INT + 1)
        bank.set_balance(1, 0)
        bank.set_balance(1, 123)
        self.assertEqual(bank.get_balance(1), 123)

    def test_inventory_rejects_non_positive_quantities_without_mutation(self):
        for amount in (0, -1):
            with self.assertRaises(ValueError):
                bank.add_item(1, "shield", amount)
        self.assertEqual(bank.get_item_count(1, "shield"), 0)
        bank.add_item(1, "shield", 2)
        self.assertEqual(bank.get_item_count(1, "shield"), 2)
        with self.assertRaises(ValueError):
            bank.add_item(1, "shield", bank.SQLITE_MAX_INT + 1)
        self.assertEqual(bank.get_item_count(1, "shield"), 2)

    def test_daily_streak_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            bank.set_daily(1, float("nan"), 2)
        with self.assertRaises(ValueError):
            bank.set_daily(1, 10, -1)
        bank.set_daily(1, 10, 3)
        self.assertEqual(bank.get_daily(1), (10.0, 3))


if __name__ == "__main__":
    unittest.main()
