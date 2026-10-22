"""Versioned SQLite schema migrations for the casino bank."""

import time


def _migration_1_core_schema(connection):
    """Create or adopt the original bank tables without replacing existing user data."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0, "
        "last_daily REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS inventory ("
        "user_id TEXT NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (user_id, item))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS whitelist ("
        "kind TEXT NOT NULL, target_id INTEGER NOT NULL, PRIMARY KEY (kind, target_id))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS guild_admins ("
        "guild_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rps_wagers ("
        "game_id TEXT PRIMARY KEY, player_a TEXT NOT NULL, player_b TEXT NOT NULL, "
        "amount INTEGER NOT NULL CHECK(amount > 0), "
        "status TEXT NOT NULL CHECK(status IN ('active', 'settled', 'refunded')), "
        "outcome TEXT, created_at REAL NOT NULL, settled_at REAL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rps_wagers_active "
        "ON rps_wagers(created_at) WHERE status='active'"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS app_settings ("
        "setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at REAL NOT NULL)"
    )


def _migration_2_economy_ledger(connection):
    """Add a durable, append-only record of economy balance transitions."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS economy_ledger ("
        "entry_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "transaction_id TEXT NOT NULL, created_at REAL NOT NULL, "
        "event_type TEXT NOT NULL, user_id TEXT NOT NULL, "
        "delta INTEGER NOT NULL, balance_after INTEGER NOT NULL, "
        "reference TEXT, metadata_json TEXT)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_economy_ledger_user_created "
        "ON economy_ledger(user_id, entry_id DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_economy_ledger_transaction "
        "ON economy_ledger(transaction_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_economy_ledger_reference "
        "ON economy_ledger(reference)"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS economy_ledger_no_update "
        "BEFORE UPDATE ON economy_ledger BEGIN "
        "SELECT RAISE(ABORT, 'economy_ledger is append-only'); END"
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS economy_ledger_no_delete "
        "BEFORE DELETE ON economy_ledger BEGIN "
        "SELECT RAISE(ABORT, 'economy_ledger is append-only'); END"
    )


def _migration_3_single_player_casino_wagers(connection):
    """Persist solo wagers so stake debit and settlement can be recovered safely."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS casino_wagers ("
        "game_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, game_type TEXT NOT NULL, "
        "stake INTEGER NOT NULL CHECK(stake > 0), choice TEXT NOT NULL, result TEXT NOT NULL, "
        "won INTEGER NOT NULL CHECK(won IN (0, 1)), "
        "win_payout INTEGER NOT NULL CHECK(win_payout >= 0), "
        "status TEXT NOT NULL CHECK(status IN ('active', 'settled', 'refunded')), "
        "outcome TEXT, payout INTEGER NOT NULL DEFAULT 0 CHECK(payout >= 0), "
        "created_at REAL NOT NULL, settled_at REAL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_casino_wagers_active "
        "ON casino_wagers(created_at) WHERE status='active'"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_casino_wagers_user_created "
        "ON casino_wagers(user_id, created_at DESC)"
    )


def _migration_4_casino_wager_message_refs(connection):
    """Remember the live Discord panel so startup recovery can close stale controls."""
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(casino_wagers)").fetchall()
    }
    if "channel_id" not in columns:
        connection.execute("ALTER TABLE casino_wagers ADD COLUMN channel_id INTEGER")
    if "message_id" not in columns:
        connection.execute("ALTER TABLE casino_wagers ADD COLUMN message_id INTEGER")


def _migration_5_rps_wager_message_refs(connection):
    """Remember each RPS challenge panel so startup can close stale controls."""
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(rps_wagers)").fetchall()
    }
    if "channel_id" not in columns:
        connection.execute("ALTER TABLE rps_wagers ADD COLUMN channel_id INTEGER")
    if "message_id" not in columns:
        connection.execute("ALTER TABLE rps_wagers ADD COLUMN message_id INTEGER")


_SCHEMA_MIGRATIONS = (
    (1, "core_schema", _migration_1_core_schema),
    (2, "economy_ledger", _migration_2_economy_ledger),
    (3, "single_player_casino_wagers", _migration_3_single_player_casino_wagers),
    (4, "casino_wager_message_refs", _migration_4_casino_wager_message_refs),
    (5, "rps_wager_message_refs", _migration_5_rps_wager_message_refs),
)


def run_migrations(connection):
    """Apply ordered, transactional schema migrations while retaining legacy data."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
    )
    connection.commit()
    applied = {
        int(row["version"])
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    latest_supported = max(version for version, _name, _migration in _SCHEMA_MIGRATIONS)
    if applied and max(applied) > latest_supported:
        raise RuntimeError(
            f"Database schema version {max(applied)} is newer than supported "
            f"version {latest_supported}"
        )

    for version, name, migration in _SCHEMA_MIGRATIONS:
        if version in applied:
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, time.time()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
