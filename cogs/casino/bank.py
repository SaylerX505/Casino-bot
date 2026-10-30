"""SQLite bank facade; feature transactions live in focused modules."""

"""SQLite bank: balances, daily, streaks, inventory, whitelist. Thread-safe + atomic."""
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from cogs.casino.bank_ledger import (
    read_entries as _read_ledger,
)
from cogs.casino.bank_ledger import (
    record_economy_event as _record_ledger,
)
from cogs.casino.bank_schema import run_migrations

# Resolve storage from the project root, not the process working directory.
DB_FILE = os.getenv("BANK_DB_FILE", str(Path(__file__).resolve().parents[2] / "data" / "bank.db"))
_lock = threading.RLock()
_conn = None
SQLITE_MAX_INT = (1 << 63) - 1

def _connect():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_FILE)), exist_ok=True)
        connection = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        _conn = connection
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            run_migrations(connection)
        except Exception:
            connection.close()
            _conn = None
            raise
    return _conn


def _ensure(uid):
    c = _connect()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (str(uid),))
    c.commit()

def fmt(n):
    try:
        return format(int(n), ",d")
    except (TypeError, ValueError, OverflowError):
        return str(n)

def now():
    return time.time()


def schema_version() -> int:
    """Return the highest successfully applied SQLite schema migration."""
    with _lock:
        row = _connect().execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0) if row is not None else 0


def get_economy_ledger(
    *,
    user_id=None,
    event_type=None,
    reference=None,
    limit=100,
):
    """Read append-only ledger records newest-first."""
    with _lock:
        return _read_ledger(
            _connect(),
            user_id=user_id,
            event_type=event_type,
            reference=reference,
            limit=limit,
        )


def get_balance(user_id):
    uid = str(user_id)
    with _lock:
        _ensure(uid)
        row = _connect().execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(row["balance"]) if row else 0

def update_balance(user_id, amount):
    """Apply an integer adjustment without overdrafts or implicit type coercion."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError("balance adjustment must be an integer")
    uid = str(user_id)
    if amount > 0:
        credited, balance = credit_balance(uid, amount)
        if not credited:
            raise OverflowError("balance would exceed SQLite's signed integer range")
        return balance
    if amount < 0:
        debited, balance = debit_balance(uid, -amount)
        if not debited:
            raise ValueError("insufficient balance for debit")
        return balance
    return get_balance(uid)


def debit_balance(user_id, amount):
    """Debit only when the account can cover the full integer amount."""
    uid = str(user_id)
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > SQLITE_MAX_INT:
        return False, get_balance(uid)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            changed = c.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
                (amount, uid, amount),
            ).rowcount
            if changed != 1:
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                balance = int(row["balance"]) if row else 0
                c.rollback()
                return False, balance
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance_after = int(row["balance"]) if row else 0
            _record_ledger(
                c,
                event_type="balance_debit",
                user_id=uid,
                delta=-amount,
                balance_after=balance_after,
            )
            c.commit()
            return True, balance_after
        except Exception:
            c.rollback()
            raise


def credit_balance(user_id, amount):
    """Credit integer coins atomically without exceeding SQLite's signed integer range."""
    uid = str(user_id)
    sqlite_max_int = SQLITE_MAX_INT
    if isinstance(amount, bool) or not isinstance(amount, int):
        return False, get_balance(uid)
    if amount <= 0 or amount > sqlite_max_int:
        return False, get_balance(uid)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            changed = c.execute(
                "UPDATE users SET balance=balance+? "
                "WHERE user_id=? AND balance<=?",
                (amount, uid, sqlite_max_int - amount),
            ).rowcount
            if changed != 1:
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                balance = int(row["balance"]) if row else 0
                c.rollback()
                return False, balance
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance_after = int(row["balance"]) if row else 0
            _record_ledger(
                c,
                event_type="balance_credit",
                user_id=uid,
                delta=amount,
                balance_after=balance_after,
            )
            c.commit()
            return True, balance_after
        except Exception:
            c.rollback()
            raise


def remove_balance(user_id, amount):
    """Admin-facing removal that cannot make an account balance negative."""
    return debit_balance(user_id, amount)


def transfer_balance(sender_id, recipient_id, amount):
    """Transfer integer coins atomically; return (success, sender_balance_after)."""
    sender, recipient = str(sender_id), str(recipient_id)
    sqlite_max_int = SQLITE_MAX_INT
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount <= 0
        or amount > sqlite_max_int
        or sender == recipient
    ):
        return False, get_balance(sender)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (sender,))
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (recipient,))
            changed = c.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
                (amount, sender, amount),
            ).rowcount
            if changed != 1:
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (sender,)).fetchone()
                balance = int(row["balance"]) if row else 0
                c.rollback()
                return False, balance
            credited = c.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=? AND balance<=?",
                (amount, recipient, sqlite_max_int - amount),
            ).rowcount
            if credited != 1:
                c.rollback()
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (sender,)).fetchone()
                return False, int(row["balance"]) if row else 0
            sender_row = c.execute(
                "SELECT balance FROM users WHERE user_id=?", (sender,)
            ).fetchone()
            recipient_row = c.execute(
                "SELECT balance FROM users WHERE user_id=?", (recipient,)
            ).fetchone()
            sender_after = int(sender_row["balance"]) if sender_row else 0
            recipient_after = int(recipient_row["balance"]) if recipient_row else 0
            transaction_id = uuid.uuid4().hex
            _record_ledger(
                c,
                event_type="balance_transfer",
                user_id=sender,
                delta=-amount,
                balance_after=sender_after,
                transaction_id=transaction_id,
                metadata={"direction": "outgoing", "counterparty": recipient},
            )
            _record_ledger(
                c,
                event_type="balance_transfer",
                user_id=recipient,
                delta=amount,
                balance_after=recipient_after,
                transaction_id=transaction_id,
                metadata={"direction": "incoming", "counterparty": sender},
            )
            c.commit()
            return True, sender_after
        except Exception:
            c.rollback()
            raise



def settle_wheel(user_id, entry_fee, payout=0, target_loss=None):
    """Settle a wheel round atomically after validating all coin amounts."""
    uid = str(user_id)
    sqlite_max_int = SQLITE_MAX_INT
    if isinstance(entry_fee, bool) or not isinstance(entry_fee, int):
        raise TypeError("entry_fee must be an integer")
    if isinstance(payout, bool) or not isinstance(payout, int):
        raise TypeError("payout must be an integer")
    if not 0 < entry_fee <= sqlite_max_int or not 0 <= payout <= sqlite_max_int:
        raise ValueError("invalid wheel settlement values")
    if target_loss is not None:
        if isinstance(target_loss, bool) or not isinstance(target_loss, int):
            raise TypeError("target_loss must be an integer")
        if not 0 <= target_loss <= sqlite_max_int or payout:
            raise ValueError("invalid wheel loss")

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            before = int(row["balance"]) if row else 0
            if before < entry_fee:
                c.rollback()
                return {"played": False, "reason": "insufficient", "before": before, "after": before, "net": 0, "refund": 0}

            after_fee = before - entry_fee
            if target_loss is not None:
                after = before - min(before, target_loss)
            else:
                if after_fee > sqlite_max_int - payout:
                    c.rollback()
                    return {"played": False, "reason": "balance_limit", "before": before, "after": before, "net": 0, "refund": 0}
                after = after_fee + payout

            if after < 0 or after > sqlite_max_int:
                c.rollback()
                return {"played": False, "reason": "balance_limit", "before": before, "after": before, "net": 0, "refund": 0}

            c.execute("UPDATE users SET balance=? WHERE user_id=?", (after, uid))
            _record_ledger(
                c,
                event_type="wheel_settlement",
                user_id=uid,
                delta=after - before,
                balance_after=after,
                metadata={
                    "before": before,
                    "entry_fee": entry_fee,
                    "payout": payout,
                    "target_loss": target_loss,
                    "refund": max(0, after - after_fee),
                },
            )
            c.commit()
            return {
                "played": True,
                "reason": None,
                "before": before,
                "after": after,
                "net": after - before,
                "refund": max(0, after - after_fee),
                "payout": payout,
                "entry_fee": entry_fee,
                "target_loss": target_loss,
            }
        except Exception:
            c.rollback()
            raise


def backup_database(destination):
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    with _lock:
        source = _connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()


def reset_all():
    """Reset wallets only when no persisted wager depends on their balances."""
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            active_rps_wager = c.execute(
                "SELECT game_id FROM rps_wagers WHERE status='active' LIMIT 1"
            ).fetchone()
            if active_rps_wager is not None:
                raise RuntimeError(
                    "cannot reset the economy while active RPS wagers exist"
                )
            active_casino_wager = c.execute(
                "SELECT game_id FROM casino_wagers WHERE status='active' LIMIT 1"
            ).fetchone()
            if active_casino_wager is not None:
                raise RuntimeError(
                    "cannot reset the economy while active casino wagers exist"
                )
            transaction_id = uuid.uuid4().hex
            for row in c.execute("SELECT user_id,balance FROM users").fetchall():
                _record_ledger(
                    c,
                    event_type="economy_reset",
                    user_id=str(row["user_id"]),
                    delta=-int(row["balance"]),
                    balance_after=0,
                    transaction_id=transaction_id,
                    reference="reset_all",
                    metadata={"previous_balance": int(row["balance"])},
                )
            c.execute("DELETE FROM users")
            c.execute("DELETE FROM inventory")
            c.commit()
        except Exception:
            c.rollback()
            raise

def migrate_from_json(data):
    counts = {"users": 0, "items": 0, "wl": 0}
    with _lock:
        c = _connect()
        for uid, bal in (data.get("balances") or {}).items():
            _ensure(str(uid))
            c.execute("UPDATE users SET balance=? WHERE user_id=?", (int(bal or 0), str(uid)))
            counts["users"] += 1
        for uid, ts in (data.get("daily_claims") or {}).items():
            _ensure(str(uid))
            c.execute("UPDATE users SET last_daily=? WHERE user_id=?", (float(ts or 0), str(uid)))
        for uid, st in (data.get("streaks") or {}).items():
            _ensure(str(uid))
            c.execute("UPDATE users SET streak=? WHERE user_id=?", (int(st or 0), str(uid)))
        for uid, items in (data.get("inventory") or {}).items():
            for item, qty in (items or {}).items():
                if int(qty or 0) > 0:
                    c.execute("INSERT OR REPLACE INTO inventory (user_id, item, qty) VALUES (?,?,?)", (str(uid), item, int(qty)))
                    counts["items"] += 1
        for u in (data.get("whitelist", {}).get("users") or []):
            c.execute("INSERT OR IGNORE INTO whitelist (kind, target_id) VALUES ('users',?)", (int(u),))
            counts["wl"] += 1
        for r in (data.get("whitelist", {}).get("roles") or []):
            c.execute("INSERT OR IGNORE INTO whitelist (kind, target_id) VALUES ('roles',?)", (int(r),))
            counts["wl"] += 1
        c.commit()
    return counts




# --- SMALL PERSISTENT APPLICATION SETTINGS ---


def close_database():
    """Close the process-local SQLite connection cleanly during shutdown."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


from cogs.casino.bank_wallet import (
    add_guild_admin,
    remove_guild_admin,
    list_guild_admins,
    is_guild_admin,
    purchase_item,
    claim_daily,
    redeem_item,
    charge_up_to,
    set_balance,
    get_daily,
    set_daily,
    get_item_count,
    get_inventory,
    top_balances,
    add_item,
    remove_item,
    get_whitelist,
    whitelist_add,
    whitelist_remove,
)

from cogs.casino.bank_settings import (
    get_setting,
    set_setting,
)

from cogs.casino.bank_rps import (
    place_bet,
    create_rps_challenge,
    set_rps_challenge_message_reference,
    rps_challenge_status,
    accept_rps_challenge,
    decline_rps_challenge,
    cancel_rps_challenge,
    _close_rps_challenge,
    expire_rps_challenge,
    expire_pending_rps_challenges,
    create_rps_wager,
    settle_rps_wager,
    refund_orphaned_rps_wagers,
    active_rps_wager_ids,
    set_rps_wager_message_reference,
    active_rps_wager_records,
    rps_wager_status,
)

from cogs.casino.bank_wagers import (
    create_casino_wager,
    double_down_casino_wager,
    play_casino_wager,
    settle_casino_wager,
    refund_orphaned_casino_wagers,
    active_casino_wager_ids,
    active_casino_wager_records,
    casino_wager_status,
)

__all__ = [
    "DB_FILE",
    "SQLITE_MAX_INT",
    "_close_rps_challenge",
    "_connect",
    "_ensure",
    "accept_rps_challenge",
    "active_casino_wager_ids",
    "active_casino_wager_records",
    "active_rps_wager_ids",
    "active_rps_wager_records",
    "add_guild_admin",
    "add_item",
    "backup_database",
    "cancel_rps_challenge",
    "casino_wager_status",
    "charge_up_to",
    "claim_daily",
    "close_database",
    "create_casino_wager",
    "create_rps_challenge",
    "create_rps_wager",
    "credit_balance",
    "debit_balance",
    "decline_rps_challenge",
    "double_down_casino_wager",
    "expire_pending_rps_challenges",
    "expire_rps_challenge",
    "fmt",
    "get_balance",
    "get_daily",
    "get_economy_ledger",
    "get_inventory",
    "get_item_count",
    "get_setting",
    "get_whitelist",
    "is_guild_admin",
    "list_guild_admins",
    "migrate_from_json",
    "now",
    "place_bet",
    "play_casino_wager",
    "purchase_item",
    "redeem_item",
    "refund_orphaned_casino_wagers",
    "refund_orphaned_rps_wagers",
    "remove_balance",
    "remove_guild_admin",
    "remove_item",
    "reset_all",
    "rps_challenge_status",
    "rps_wager_status",
    "schema_version",
    "set_balance",
    "set_daily",
    "set_rps_challenge_message_reference",
    "set_rps_wager_message_reference",
    "set_setting",
    "settle_casino_wager",
    "settle_rps_wager",
    "settle_wheel",
    "top_balances",
    "transfer_balance",
    "update_balance",
    "whitelist_add",
    "whitelist_remove",
]
