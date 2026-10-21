"""Regression tests for restart-safe RPS escrow and idempotent settlement."""
import os
import tempfile
import unittest

from cogs.casino import bank


class RPSWagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_file = bank.DB_FILE
        self.previous_connection = bank._conn
        bank.DB_FILE = os.path.join(self.temp_dir.name, "bank.db")
        bank._conn = None
        bank._connect()
        self.assertTrue(bank.credit_balance(101, 1_000)[0])
        self.assertTrue(bank.credit_balance(202, 1_000)[0])

    def tearDown(self):
        connection = bank._conn
        if connection is not None:
            connection.close()
        bank._conn = self.previous_connection
        bank.DB_FILE = self.previous_db_file
        self.temp_dir.cleanup()

    def test_winner_settlement_pays_once_and_applies_tax(self):
        self.assertTrue(bank.create_rps_wager("match-1", 101, 202, 100))
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)

        result = bank.settle_rps_wager("match-1", winner_id=101)
        self.assertTrue(result["settled"])
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["payout"], 190)
        self.assertEqual(result["tax"], 10)
        self.assertEqual(bank.get_balance(101), 1_090)
        self.assertEqual(bank.get_balance(202), 900)

        repeated = bank.settle_rps_wager("match-1", winner_id=202)
        self.assertTrue(repeated["already_settled"])
        self.assertEqual(bank.get_balance(101), 1_090)
        self.assertEqual(bank.get_balance(202), 900)

    def test_orphaned_wager_is_refunded_once_after_restart(self):
        self.assertTrue(bank.create_rps_wager("match-restart", 101, 202, 100))
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)

        self.assertEqual(bank.refund_orphaned_rps_wagers(), 1)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)
        self.assertEqual(bank.refund_orphaned_rps_wagers(), 0)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)

    def test_insufficient_funds_never_partially_debits(self):
        bank.set_balance(202, 50)

        self.assertFalse(bank.create_rps_wager("match-insufficient", 101, 202, 100))
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 50)

    def test_non_participant_cannot_settle_a_wager(self):
        self.assertTrue(bank.create_rps_wager("match-invalid-winner", 101, 202, 100))

        with self.assertRaises(ValueError):
            bank.settle_rps_wager("match-invalid-winner", winner_id=303)

        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)
        self.assertEqual(bank.refund_orphaned_rps_wagers(), 1)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)


if __name__ == "__main__":
    unittest.main()
