"""Tests for persistent application settings stored in the SQLite database."""
import os
import tempfile
import unittest

from cogs.casino import bank


class ApplicationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_file = bank.DB_FILE
        self.previous_connection = bank._conn
        bank.DB_FILE = os.path.join(self.temp_dir.name, "settings.db")
        bank._conn = None

    def tearDown(self):
        connection = bank._conn
        if connection is not None:
            connection.close()
        bank._conn = self.previous_connection
        bank.DB_FILE = self.previous_db_file
        self.temp_dir.cleanup()

    def test_missing_setting_returns_default_and_persisted_values_round_trip(self):
        self.assertEqual(bank.get_setting("ai_enabled", "true"), "true")
        bank.set_setting("ai_enabled", "false")
        self.assertEqual(bank.get_setting("ai_enabled", "true"), "false")
        bank.set_setting("ai_enabled", "true")
        self.assertEqual(bank.get_setting("ai_enabled"), "true")

    def test_setting_reads_do_not_run_schema_ddl_or_commit(self):
        bank._connect()
        statements = []
        bank._conn.set_trace_callback(statements.append)
        try:
            self.assertIsNone(bank.get_setting("missing_setting"))
        finally:
            bank._conn.set_trace_callback(None)

        normalized = [statement.strip().upper() for statement in statements]
        self.assertTrue(any(statement.startswith("SELECT ") for statement in normalized))
        self.assertFalse(any("CREATE TABLE" in statement for statement in normalized))
        self.assertNotIn("COMMIT", normalized)

    def test_setting_key_must_be_non_empty(self):
        with self.assertRaises(ValueError):
            bank.set_setting("", "value")
        with self.assertRaises(ValueError):
            bank.get_setting("  ")


if __name__ == "__main__":
    unittest.main()
