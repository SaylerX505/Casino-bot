"""Regression tests for SQLite schema migrations and append-only economy ledger."""

import os
import sqlite3
import tempfile
import unittest

from cogs.casino import bank


class BankMigrationsAndLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = bank.DB_FILE
        self.original_connection = bank._conn
        bank.DB_FILE = os.path.join(self.temp_dir.name, "bank.db")
        bank._conn = None

    def tearDown(self):
        if bank._conn is not None:
            bank._conn.close()
        bank._conn = self.original_connection
        bank.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def test_schema_is_versioned_and_migrations_are_idempotent(self):
        bank.set_balance(10, 100)
        self.assertEqual(bank.schema_version(), 6)
        connection = bank._connect()
        versions = [
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6])

        bank.close_database()
        self.assertEqual(bank.get_balance(10), 100)
        self.assertEqual(bank.schema_version(), 5)
        self.assertEqual(
            [row["version"] for row in bank._connect().execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )],
            [1, 2, 3, 4, 5, 6],
        )

    def test_migrations_adopt_legacy_tables_without_discarding_data(self):
        connection = sqlite3.connect(bank.DB_FILE)
        try:
            connection.execute(
                "CREATE TABLE users (user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0, "
                "last_daily REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute(
                "INSERT INTO users (user_id,balance,last_daily,streak) VALUES ('99',321,0,0)"
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(bank.get_balance(99), 321)
        self.assertEqual(bank.schema_version(), 5)
        self.assertEqual(bank.get_economy_ledger(user_id=99), [])

    def test_balance_operations_write_ledger_entries_atomically(self):
        bank.set_balance(1, 100)
        self.assertTrue(bank.credit_balance(1, 25)[0])
        self.assertTrue(bank.debit_balance(1, 10)[0])

        entries = bank.get_economy_ledger(user_id=1, limit=10)
        self.assertEqual(
            [entry["event_type"] for entry in entries[:3]],
            ["balance_debit", "balance_credit", "balance_set"],
        )
        self.assertEqual([entry["delta"] for entry in entries[:3]], [-10, 25, 100])
        self.assertEqual([entry["balance_after"] for entry in entries[:3]], [115, 125, 100])

    def test_transfer_ledger_rows_share_one_transaction(self):
        bank.set_balance(2, 100)
        bank.set_balance(3, 5)
        self.assertTrue(bank.transfer_balance(2, 3, 40)[0])

        entries = bank.get_economy_ledger(event_type="balance_transfer", limit=2)
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry["delta"] for entry in entries}, {-40, 40})
        self.assertEqual(len({entry["transaction_id"] for entry in entries}), 1)
        self.assertEqual({entry["balance_after"] for entry in entries}, {60, 45})

    def test_rps_settlement_ledger_is_idempotent_and_correlated(self):
        bank.set_balance(101, 1_000)
        bank.set_balance(202, 1_000)
        self.assertTrue(bank.create_rps_wager("ledger-rps-1", 101, 202, 100))
        first = bank.settle_rps_wager("ledger-rps-1", winner_id=101)
        repeated = bank.settle_rps_wager("ledger-rps-1", winner_id=202)

        self.assertFalse(first["already_settled"])
        self.assertTrue(repeated["already_settled"])
        stake_entries = bank.get_economy_ledger(event_type="rps_wager_stake", reference="ledger-rps-1")
        payout_entries = bank.get_economy_ledger(event_type="rps_wager_payout", reference="ledger-rps-1")
        self.assertEqual(len(stake_entries), 2)
        self.assertEqual(len({entry["transaction_id"] for entry in stake_entries}), 1)
        self.assertEqual(len(payout_entries), 1)
        self.assertEqual(payout_entries[0]["delta"], 190)
        self.assertEqual(payout_entries[0]["metadata"]["tax"], 10)

    def test_ledger_rejects_update_and_delete(self):
        bank.set_balance(7, 20)
        connection = bank._connect()
        entry_id = bank.get_economy_ledger(user_id=7, limit=1)[0]["entry_id"]
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE economy_ledger SET delta=0 WHERE entry_id=?", (entry_id,))
        connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM economy_ledger WHERE entry_id=?", (entry_id,))
        connection.rollback()
        self.assertEqual(bank.get_economy_ledger(user_id=7, limit=1)[0]["delta"], 20)

    def test_ledger_read_api_validates_limit(self):
        for invalid in (True, 0, 1001, "10"):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                bank.get_economy_ledger(limit=invalid)


if __name__ == "__main__":
    unittest.main()
