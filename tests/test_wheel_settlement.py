"""Regression tests for atomic wheel settlement and server admin access."""
import os
import tempfile
import unittest

from cogs.casino import bank


class WheelSettlementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_file = bank.DB_FILE
        self.original_conn = bank._conn
        if bank._conn is not None:
            bank._conn.close()
        bank._conn = None
        bank.DB_FILE = os.path.join(self.tempdir.name, "bank-test.db")

    def tearDown(self):
        if bank._conn is not None:
            bank._conn.close()
        bank._conn = self.original_conn
        bank.DB_FILE = self.original_db_file
        self.tempdir.cleanup()

    def test_negative_segment_is_total_loss_not_entry_fee_plus_loss(self):
        bank.set_balance(1, 200)

        result = bank.settle_wheel(1, 50, target_loss=50)

        self.assertTrue(result["played"])
        self.assertEqual(result["before"], 200)
        self.assertEqual(result["after"], 150)
        self.assertEqual(result["net"], -50)
        self.assertEqual(bank.get_balance(1), 150)

    def test_small_loss_refunds_unused_entry_fee_atomically(self):
        bank.set_balance(2, 200)

        result = bank.settle_wheel(2, 50, target_loss=30)

        self.assertTrue(result["played"])
        self.assertEqual(result["refund"], 20)
        self.assertEqual(result["after"], 170)
        self.assertEqual(result["net"], -30)

    def test_reward_settles_entry_fee_and_payout_atomically(self):
        bank.set_balance(3, 200)

        result = bank.settle_wheel(3, 50, payout=100)

        self.assertTrue(result["played"])
        self.assertEqual(result["after"], 250)
        self.assertEqual(result["net"], 50)

    def test_insufficient_balance_is_not_charged(self):
        bank.set_balance(4, 49)

        result = bank.settle_wheel(4, 50, payout=100)

        self.assertFalse(result["played"])
        self.assertEqual(result["reason"], "insufficient")
        self.assertEqual(bank.get_balance(4), 49)

    def test_overflow_rolls_back_the_entry_fee(self):
        bank.set_balance(5, (1 << 63) - 1)

        result = bank.settle_wheel(5, 50, payout=100)

        self.assertFalse(result["played"])
        self.assertEqual(result["reason"], "balance_limit")
        self.assertEqual(bank.get_balance(5), (1 << 63) - 1)

    def test_admin_access_is_scoped_to_server(self):
        self.assertTrue(bank.add_guild_admin(100, 200))
        self.assertFalse(bank.add_guild_admin(100, 200))
        self.assertTrue(bank.is_guild_admin(100, 200))
        self.assertFalse(bank.is_guild_admin(101, 200))
        self.assertEqual(bank.list_guild_admins(100), [200])
        self.assertTrue(bank.remove_guild_admin(100, 200))
        self.assertFalse(bank.is_guild_admin(100, 200))


if __name__ == "__main__":
    unittest.main()
