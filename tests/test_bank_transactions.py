"""Regression tests for atomic bank operations against a temporary SQLite DB."""
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from cogs.casino import bank


class BankTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_file = bank.DB_FILE
        self.original_conn = bank._conn
        bank._conn = None
        bank.DB_FILE = os.path.join(self.tempdir.name, "bank-test.db")

    def tearDown(self):
        if bank._conn is not None:
            bank._conn.close()
        bank._conn = self.original_conn
        bank.DB_FILE = self.original_db_file
        self.tempdir.cleanup()

    def test_transfer_is_atomic_and_prevents_overdraft(self):
        bank.set_balance(1, 100)
        bank.set_balance(2, 5)
        ok, sender_balance = bank.transfer_balance(1, 2, 60)
        self.assertTrue(ok)
        self.assertEqual(sender_balance, 40)
        self.assertEqual(bank.get_balance(2), 65)

        ok, sender_balance = bank.transfer_balance(1, 2, 50)
        self.assertFalse(ok)
        self.assertEqual(sender_balance, 40)
        self.assertEqual(bank.get_balance(2), 65)

    def test_concurrent_transfers_cannot_overspend(self):
        bank.set_balance(10, 100)

        def transfer(recipient):
            return bank.transfer_balance(10, recipient, 10)[0]

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(transfer, range(100, 120)))

        self.assertEqual(sum(results), 10)
        self.assertEqual(bank.get_balance(10), 0)
        self.assertEqual(sum(bank.get_balance(uid) for uid in range(100, 120)), 100)

    def test_purchase_charges_and_adds_inventory_atomically(self):
        bank.set_balance(20, 500)
        ok, balance = bank.purchase_item(20, "shield", 2, 275)
        self.assertFalse(ok)
        self.assertEqual(balance, 500)
        self.assertEqual(bank.get_item_count(20, "shield"), 0)

        ok, balance = bank.purchase_item(20, "shield", 1, 275)
        self.assertTrue(ok)
        self.assertEqual(balance, 225)
        self.assertEqual(bank.get_item_count(20, "shield"), 1)

    def test_daily_claim_can_only_be_applied_once_concurrently(self):
        now = 2_000_000_000.0
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            return bank.claim_daily(30, now, 20)["claimed"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _idx: claim(), range(2)))

        self.assertEqual(sum(results), 1)
        self.assertEqual(bank.get_balance(30), 20)
        last, streak = bank.get_daily(30)
        self.assertEqual(last, now)
        self.assertEqual(streak, 1)

    def test_item_redemption_consumes_and_rewards_together(self):
        bank.set_balance(40, 10)
        bank.add_item(40, "loot_box", 1)
        ok, balance = bank.redeem_item(40, "loot_box", 50)
        self.assertTrue(ok)
        self.assertEqual(balance, 60)
        self.assertEqual(bank.get_item_count(40, "loot_box"), 0)

        ok, balance = bank.redeem_item(40, "loot_box", 50)
        self.assertFalse(ok)
        self.assertEqual(balance, 60)

    def test_rps_stakes_are_collected_together(self):
        bank.set_balance(50, 100)
        bank.set_balance(51, 5)
        self.assertFalse(bank.place_bet(50, 51, 10))
        self.assertEqual(bank.get_balance(50), 100)
        self.assertEqual(bank.get_balance(51), 5)

        self.assertTrue(bank.place_bet(50, 51, 5))
        self.assertEqual(bank.get_balance(50), 95)
        self.assertEqual(bank.get_balance(51), 0)

    def test_wheel_penalties_never_overdraw(self):
        bank.set_balance(60, 7)
        charged, balance = bank.charge_up_to(60, 50)
        self.assertEqual(charged, 7)
        self.assertEqual(balance, 0)
        self.assertEqual(bank.get_balance(60), 0)

    def test_item_removal_cannot_overconsume_inventory(self):
        bank.add_item(70, "shield", 8)

        def remove_one(_):
            return bank.remove_item(70, "shield", 1)

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(remove_one, range(20)))

        self.assertEqual(sum(results), 8)
        self.assertEqual(bank.get_item_count(70, "shield"), 0)
        self.assertFalse(bank.remove_item(70, "shield", 0))
        self.assertFalse(bank.remove_item(70, "shield", -1))

    def test_purchase_rejects_amounts_outside_sqlite_integer_range(self):
        bank.set_balance(80, 100)
        ok, balance = bank.purchase_item(80, "shield", 10**30, 275)
        self.assertFalse(ok)
        self.assertEqual(balance, 100)
        self.assertEqual(bank.get_item_count(80, "shield"), 0)

    def test_credit_rejects_sqlite_integer_overflow(self):
        max_balance = (1 << 63) - 1
        starting_balance = max_balance - 5
        bank.set_balance(90, starting_balance)

        ok, balance = bank.credit_balance(90, 10)
        self.assertFalse(ok)
        self.assertEqual(balance, starting_balance)

        ok, balance = bank.credit_balance(90, 5)
        self.assertTrue(ok)
        self.assertEqual(balance, max_balance)

    def test_transfer_rolls_back_when_recipient_would_overflow(self):
        bank.set_balance(91, 100)
        bank.set_balance(92, (1 << 63) - 5)
        ok, sender_balance = bank.transfer_balance(91, 92, 10)
        self.assertFalse(ok)
        self.assertEqual(sender_balance, 100)
        self.assertEqual(bank.get_balance(91), 100)
        self.assertEqual(bank.get_balance(92), (1 << 63) - 5)

    def test_oversized_debits_and_transfers_fail_without_sql_errors(self):
        bank.set_balance(93, 100)
        self.assertFalse(bank.debit_balance(93, 10**30)[0])
        self.assertFalse(bank.transfer_balance(93, 94, 10**30)[0])
        self.assertEqual(bank.get_balance(93), 100)

    def test_generic_balance_adjustment_rejects_overflow(self):
        max_balance = (1 << 63) - 1
        bank.set_balance(101, max_balance)
        with self.assertRaises(OverflowError):
            bank.update_balance(101, 1)
        self.assertEqual(bank.get_balance(101), max_balance)

    def test_generic_debit_rejects_overdraft(self):
        bank.set_balance(104, 5)
        with self.assertRaises(ValueError):
            bank.update_balance(104, -6)
        self.assertEqual(bank.get_balance(104), 5)
        self.assertEqual(bank.update_balance(104, -5), 0)

    def test_daily_reward_does_not_overflow_or_consume_claim(self):
        max_balance = (1 << 63) - 1
        bank.set_balance(102, max_balance - 5)
        claim = bank.claim_daily(102, 2_000_000_000.0, 20)
        self.assertFalse(claim["claimed"])
        self.assertEqual(claim["reason"], "balance_limit")
        self.assertEqual(bank.get_balance(102), max_balance - 5)
        self.assertEqual(bank.get_daily(102), (0.0, 0))

    def test_item_is_not_consumed_when_reward_would_overflow(self):
        max_balance = (1 << 63) - 1
        bank.set_balance(103, max_balance - 5)
        bank.add_item(103, "loot_box", 1)
        redeemed, balance = bank.redeem_item(103, "loot_box", 10)
        self.assertFalse(redeemed)
        self.assertEqual(balance, max_balance - 5)
        self.assertEqual(bank.get_item_count(103, "loot_box"), 1)


if __name__ == "__main__":
    unittest.main()
