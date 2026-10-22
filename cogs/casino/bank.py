# --- SQLITE BANK ---
"""SQLite bank: balances, daily, streaks, inventory, whitelist. Thread-safe + atomic."""
import math
import os
import sqlite3
import threading
import time
from pathlib import Path

SQLITE_MAX_INTEGER = (1 << 63) - 1

# Resolve storage from the project root, not the process working directory.
DB_FILE = os.getenv("BANK_DB_FILE", str(Path(__file__).resolve().parents[2] / "data" / "bank.db"))
_lock = threading.RLock()
_conn = None
SQLITE_MAX_INT = (1 << 63) - 1

def _connect():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_FILE)), exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=30000")
        _conn.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0, last_daily REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0)")
        _conn.execute("CREATE TABLE IF NOT EXISTS inventory (user_id TEXT NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, item))")
        _conn.execute("CREATE TABLE IF NOT EXISTS whitelist (kind TEXT NOT NULL, target_id INTEGER NOT NULL, PRIMARY KEY (kind, target_id))")
        _conn.execute("CREATE TABLE IF NOT EXISTS guild_admins (guild_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))")
        _conn.execute("""CREATE TABLE IF NOT EXISTS rps_wagers (
            game_id TEXT PRIMARY KEY,
            player_a TEXT NOT NULL,
            player_b TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            status TEXT NOT NULL CHECK(status IN ('active', 'settled', 'refunded')),
            outcome TEXT,
            created_at REAL NOT NULL,
            settled_at REAL
        )""")
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rps_wagers_active "
            "ON rps_wagers(created_at) WHERE status='active'"
        )
        _conn.commit()
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

def get_balance(user_id):
    uid = str(user_id)
    with _lock:
        _ensure(uid)
        row = _connect().execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(row["balance"]) if row else 0

def update_balance(user_id, amount):
    """Apply a safe adjustment without allowing overdrafts or integer overflow."""
    uid, amount = str(user_id), int(amount)
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
    """Debit only when the account can cover the full amount."""
    uid, amount = str(user_id), int(amount)
    if amount <= 0 or amount > (1 << 63) - 1:
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
            c.commit()
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            return True, int(row["balance"]) if row else 0
        except Exception:
            c.rollback()
            raise


def credit_balance(user_id, amount):
    """Credit coins atomically without exceeding SQLite's signed integer range."""
    uid, amount = str(user_id), int(amount)
    sqlite_max_int = (1 << 63) - 1
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
            c.commit()
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            return True, int(row["balance"]) if row else 0
        except Exception:
            c.rollback()
            raise


def remove_balance(user_id, amount):
    """Admin-facing removal that cannot make an account balance negative."""
    return debit_balance(user_id, amount)


def transfer_balance(sender_id, recipient_id, amount):
    """Transfer coins atomically; return (success, sender_balance_after)."""
    sender, recipient, amount = str(sender_id), str(recipient_id), int(amount)
    sqlite_max_int = (1 << 63) - 1
    if amount <= 0 or amount > sqlite_max_int or sender == recipient:
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
            c.commit()
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (sender,)).fetchone()
            return True, int(row["balance"]) if row else 0
        except Exception:
            c.rollback()
            raise


def settle_wheel(user_id, entry_fee, payout=0, target_loss=None):
    uid, entry_fee, payout = str(user_id), int(entry_fee), int(payout)
    sqlite_max_int = (1 << 63) - 1
    if entry_fee <= 0 or payout < 0 or payout > sqlite_max_int:
        raise ValueError("invalid wheel settlement values")
    if target_loss is not None:
        target_loss = int(target_loss)
        if target_loss < 0 or target_loss > sqlite_max_int or payout:
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


def add_guild_admin(guild_id, user_id):
    with _lock:
        c = _connect()
        changed = c.execute(
            "INSERT OR IGNORE INTO guild_admins (guild_id, user_id) VALUES (?, ?)",
            (str(guild_id), str(user_id)),
        ).rowcount
        c.commit()
        return changed == 1


def remove_guild_admin(guild_id, user_id):
    with _lock:
        c = _connect()
        changed = c.execute(
            "DELETE FROM guild_admins WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ).rowcount
        c.commit()
        return changed == 1


def list_guild_admins(guild_id):
    with _lock:
        rows = _connect().execute(
            "SELECT user_id FROM guild_admins WHERE guild_id=? ORDER BY user_id",
            (str(guild_id),),
        ).fetchall()
        return [int(row["user_id"]) for row in rows]


def is_guild_admin(guild_id, user_id):
    with _lock:
        row = _connect().execute(
            "SELECT 1 FROM guild_admins WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ).fetchone()
        return row is not None

def purchase_item(user_id, item, quantity, unit_price):
    """Charge coins and add inventory in one transaction."""
    uid, quantity, unit_price = str(user_id), int(quantity), int(unit_price)
    sqlite_max_int = (1 << 63) - 1
    if (
        quantity <= 0
        or unit_price < 0
        or quantity > sqlite_max_int
        or unit_price > sqlite_max_int
        or (unit_price and quantity > sqlite_max_int // unit_price)
    ):
        return False, get_balance(uid)
    total = quantity * unit_price
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            changed = c.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
                (total, uid, total),
            ).rowcount
            if changed != 1:
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                balance = int(row["balance"]) if row else 0
                c.rollback()
                return False, balance
            c.execute(
                "INSERT INTO inventory (user_id,item,qty) VALUES (?,?,?) "
                "ON CONFLICT(user_id,item) DO UPDATE SET qty=qty+excluded.qty",
                (uid, str(item), quantity),
            )
            c.commit()
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            return True, int(row["balance"]) if row else 0
        except Exception:
            c.rollback()
            raise


def claim_daily(user_id, now_ts, base_reward):
    """Atomically enforce cooldown, update streak, and grant the daily reward."""
    uid, now_ts, base_reward = str(user_id), float(now_ts), int(base_reward)
    if base_reward < 0:
        raise ValueError("base_reward must not be negative")
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute(
                "SELECT balance,last_daily,streak FROM users WHERE user_id=?", (uid,)
            ).fetchone()
            balance, last, streak = int(row["balance"]), float(row["last_daily"]), int(row["streak"])
            if now_ts - last < 86400:
                c.rollback()
                return {"claimed": False, "last_daily": last, "streak": streak,
                        "amount": 0, "balance": balance}
            if last and (now_ts - last) > 86400 * 2:
                streak = 0
            reward = base_reward + min(streak, 7) * 10
            if balance > ((1 << 63) - 1) - reward:
                c.rollback()
                return {"claimed": False, "reason": "balance_limit", "last_daily": last,
                        "streak": streak, "amount": 0, "balance": balance}
            new_streak = streak + 1
            c.execute(
                "UPDATE users SET balance=balance+?,last_daily=?,streak=? WHERE user_id=?",
                (reward, now_ts, new_streak, uid),
            )
            c.commit()
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            return {"claimed": True, "last_daily": now_ts, "streak": new_streak,
                    "amount": reward, "balance": int(row["balance"]) if row else 0}
        except Exception:
            c.rollback()
            raise


def redeem_item(user_id, item, reward):
    """Consume one inventory item and grant its reward atomically."""
    uid, item, reward = str(user_id), str(item), int(reward)
    if reward < 0:
        raise ValueError("reward must not be negative")
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            balance_row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = int(balance_row["balance"]) if balance_row else 0
            if balance > ((1 << 63) - 1) - reward:
                c.rollback()
                return False, balance
            changed = c.execute(
                "UPDATE inventory SET qty=qty-1 WHERE user_id=? AND item=? AND qty>0",
                (uid, item),
            ).rowcount
            if changed != 1:
                c.rollback()
                return False, balance
            c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, uid))
            c.commit()
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            return True, int(row["balance"]) if row else 0
        except Exception:
            c.rollback()
            raise


def place_bet(player_a, player_b, amount):
    """Collect both stakes atomically; neither player is charged on failure."""
    a, b, amount = str(player_a), str(player_b), int(amount)
    if amount <= 0 or amount > (1 << 63) - 1 or a == b:
        return False
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (a,))
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (b,))
            row_a = c.execute("SELECT balance FROM users WHERE user_id=?", (a,)).fetchone()
            row_b = c.execute("SELECT balance FROM users WHERE user_id=?", (b,)).fetchone()
            if not row_a or int(row_a["balance"]) < amount:
                c.rollback()
                return False
            if not row_b or int(row_b["balance"]) < amount:
                c.rollback()
                return False
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, a))
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, b))
            c.commit()
            return True
        except Exception:
            c.rollback()
            raise



def create_rps_wager(game_id, player_a, player_b, amount):
    """Atomically debit both stakes and persist them as an recoverable wager."""
    game_id, a, b, amount = str(game_id), str(player_a), str(player_b), int(amount)
    if not game_id or a == b or amount <= 0 or amount > (1 << 63) - 1:
        return False

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM rps_wagers WHERE game_id=?", (game_id,)).fetchone():
                c.rollback()
                return False

            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (a,))
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (b,))
            row_a = c.execute("SELECT balance FROM users WHERE user_id=?", (a,)).fetchone()
            row_b = c.execute("SELECT balance FROM users WHERE user_id=?", (b,)).fetchone()
            if not row_a or int(row_a["balance"]) < amount or not row_b or int(row_b["balance"]) < amount:
                c.rollback()
                return False

            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, a))
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, b))
            c.execute(
                "INSERT INTO rps_wagers "
                "(game_id,player_a,player_b,amount,status,created_at) VALUES (?,?,?,?,'active',?)",
                (game_id, a, b, amount, now()),
            )
            c.commit()
            return True
        except Exception:
            c.rollback()
            raise


def settle_rps_wager(game_id, winner_id=None):
    """Settle or refund a persisted RPS wager exactly once."""
    game_id = str(game_id)
    sqlite_max_int = (1 << 63) - 1
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            wager = c.execute(
                "SELECT player_a,player_b,amount,status FROM rps_wagers WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if wager is None:
                c.rollback()
                return {"settled": False, "reason": "not_found"}
            if wager["status"] != "active":
                c.rollback()
                return {
                    "settled": True,
                    "already_settled": True,
                    "status": wager["status"],
                    "reason": None,
                    "payout": 0,
                    "tax": 0,
                }

            a, b, amount = str(wager["player_a"]), str(wager["player_b"]), int(wager["amount"])
            if winner_id is not None and str(winner_id) not in (a, b):
                c.rollback()
                raise ValueError("winner is not a participant in this wager")

            rows = c.execute(
                "SELECT user_id,balance FROM users WHERE user_id IN (?,?)", (a, b)
            ).fetchall()
            balances = {str(row["user_id"]): int(row["balance"]) for row in rows}
            if a not in balances or b not in balances:
                c.rollback()
                raise RuntimeError("RPS wager references a missing wallet")

            status = "refunded"
            outcome = "refund"
            reason = None
            payout = 0
            tax = 0

            def refund_stakes():
                if balances[a] > sqlite_max_int - amount or balances[b] > sqlite_max_int - amount:
                    return False
                c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, a))
                c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, b))
                return True

            if winner_id is not None:
                winner = str(winner_id)
                pot = amount * 2
                payout = (pot * 95) // 100
                tax = pot - payout
                if balances[winner] <= sqlite_max_int - payout:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (payout, winner))
                    status = "settled"
                    outcome = "winner:" + winner
                else:
                    if not refund_stakes():
                        c.rollback()
                        return {"settled": False, "reason": "balance_limit"}
                    reason = "balance_limit"
                    payout = 0
                    tax = 0
            elif not refund_stakes():
                c.rollback()
                return {"settled": False, "reason": "balance_limit"}

            c.execute(
                "UPDATE rps_wagers SET status=?, outcome=?, settled_at=? "
                "WHERE game_id=? AND status='active'",
                (status, outcome, now(), game_id),
            )
            c.commit()
            return {
                "settled": True,
                "already_settled": False,
                "status": status,
                "reason": reason,
                "winner_id": str(winner_id) if winner_id is not None and status == "settled" else None,
                "payout": payout,
                "tax": tax,
            }
        except Exception:
            c.rollback()
            raise


def refund_orphaned_rps_wagers():
    """Refund interrupted RPS wagers, isolating errors so one record cannot block recovery."""
    import logging

    logger = logging.getLogger(__name__)
    with _lock:
        rows = _connect().execute(
            "SELECT game_id FROM rps_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        game_ids = [str(row["game_id"]) for row in rows]

    refunded = 0
    pending = 0
    for game_id in game_ids:
        try:
            result = settle_rps_wager(game_id)
        except Exception:
            pending += 1
            logger.exception(
                "Could not recover orphaned RPS wager game_id=%s; escrow remains recorded",
                game_id,
            )
            continue

        if not result.get("settled"):
            pending += 1
            logger.error(
                "Orphaned RPS wager remains active game_id=%s reason=%s",
                game_id,
                result.get("reason", "unknown"),
            )
        elif result.get("status") == "refunded":
            refunded += 1

    if pending:
        logger.error(
            "%s orphaned RPS wager(s) remain active and require retry or operator reconciliation",
            pending,
        )
    return refunded


def active_rps_wager_ids():
    """Return active escrow IDs in stable order for startup recovery workers."""
    with _lock:
        rows = _connect().execute(
            "SELECT game_id FROM rps_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [str(row["game_id"]) for row in rows]


def charge_up_to(user_id, amount):
    """Charge at most the available balance; return (charged, new_balance)."""
    uid, amount = str(user_id), int(amount)
    if amount <= 0 or amount > (1 << 63) - 1:
        return 0, get_balance(uid)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = max(0, int(row["balance"])) if row else 0
            charged = min(balance, amount)
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (charged, uid))
            c.commit()
            return charged, balance - charged
        except Exception:
            c.rollback()
            raise

def set_balance(user_id, amount):
    """Set a wallet balance to a non-negative SQLite-safe integer."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError("balance must be an integer")
    if amount < 0 or amount > SQLITE_MAX_INT:
        raise ValueError("balance must be within SQLite's non-negative integer range")

    uid = str(user_id)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, uid))
            c.commit()
        except Exception:
            c.rollback()
            raise
def get_daily(uid):
    uid = str(uid)
    with _lock:
        _ensure(uid)
        row = _connect().execute("SELECT last_daily, streak FROM users WHERE user_id=?", (uid,)).fetchone()
        return float(row["last_daily"]), int(row["streak"])

def set_daily(uid, last, streak):
    """Persist a valid daily timestamp and non-negative integer streak."""
    if isinstance(last, bool):
        raise TypeError("Daily timestamp must be numeric")
    try:
        last_daily = float(last)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Daily timestamp must be finite and non-negative") from None
    if not math.isfinite(last_daily) or last_daily < 0:
        raise ValueError("Daily timestamp must be finite and non-negative")
    if isinstance(streak, bool) or not isinstance(streak, int) or streak < 0:
        raise ValueError("Daily streak must be a non-negative integer")
    if streak > SQLITE_MAX_INT:
        raise ValueError("Daily streak exceeds SQLite's integer range")

    uid = str(uid)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            c.execute(
                "UPDATE users SET last_daily=?, streak=? WHERE user_id=?",
                (last_daily, streak, uid),
            )
            c.commit()
        except Exception:
            c.rollback()
            raise
def get_item_count(user_id, item):
    with _lock:
        row = _connect().execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (str(user_id), item)).fetchone()
        return int(row["qty"]) if row else 0

def get_inventory(user_id):
    with _lock:
        rows = _connect().execute("SELECT item, qty FROM inventory WHERE user_id=?", (str(user_id),)).fetchall()
        return {r["item"]: int(r["qty"]) for r in rows}

def top_balances(limit=10):
    with _lock:
        rows = _connect().execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT ?", (int(limit),)).fetchall()
        return [(r["user_id"], int(r["balance"])) for r in rows]

def add_item(user_id, item, amount=1):
    """Add a strictly positive quantity without overflowing SQLite's integer range."""
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= SQLITE_MAX_INT:
        raise ValueError("item quantity must be a positive SQLite-safe integer")
    if not isinstance(item, str) or not item.strip():
        raise ValueError("item name is required")

    uid = str(user_id)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute(
                "SELECT qty FROM inventory WHERE user_id=? AND item=?", (uid, item)
            ).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO inventory (user_id, item, qty) VALUES (?,?,?)",
                    (uid, item, amount),
                )
            else:
                current = int(row["qty"])
                if current < 0 or current > SQLITE_MAX_INT - amount:
                    raise OverflowError("inventory quantity would exceed SQLite's integer range")
                c.execute(
                    "UPDATE inventory SET qty=qty+? WHERE user_id=? AND item=?",
                    (amount, uid, item),
                )
            c.commit()
        except Exception:
            c.rollback()
            raise
def remove_item(user_id, item, amount=1):
    """Atomically consume inventory only when sufficient quantity exists."""
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= SQLITE_MAX_INT:
        return False
    if not isinstance(item, str) or not item.strip():
        return False

    uid = str(user_id)
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE inventory SET qty = qty - ? "
                "WHERE user_id = ? AND item = ? AND qty >= ?",
                (amount, uid, item, amount),
            ).rowcount
            if changed != 1:
                c.rollback()
                return False
            c.commit()
            return True
        except Exception:
            c.rollback()
            raise
def get_whitelist():
    with _lock:
        rows = _connect().execute("SELECT kind, target_id FROM whitelist").fetchall()
        out = {"users": [], "roles": []}
        for r in rows:
            if r["kind"] in out:
                out[r["kind"]].append(int(r["target_id"]))
        return out

def whitelist_add(kind, target_id):
    with _lock:
        _connect().execute("INSERT OR IGNORE INTO whitelist (kind, target_id) VALUES (?,?)", (str(kind), int(target_id)))
        _connect().commit()

def whitelist_remove(kind, target_id):
    with _lock:
        _connect().execute("DELETE FROM whitelist WHERE kind=? AND target_id=?", (str(kind), int(target_id)))
        _connect().commit()

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
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
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
def get_setting(key, default=None):
    """Read a non-sensitive application setting from SQLite, not a tracked JSON file."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("setting key must be a non-empty string")
    with _lock:
        connection = _connect()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_settings ("
            "setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        connection.commit()
        row = connection.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key=?", (key.strip(),)
        ).fetchone()
        return row["setting_value"] if row is not None else default


def set_setting(key, value):
    """Atomically persist a small application setting in the existing SQLite database."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("setting key must be a non-empty string")
    value = str(value)
    with _lock:
        connection = _connect()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_settings ("
            "setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO app_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(setting_key) DO UPDATE SET "
            "setting_value=excluded.setting_value, updated_at=excluded.updated_at",
            (key.strip(), value, time.time()),
        )
        connection.commit()


def close_database():
    """Close the process-local SQLite connection cleanly during shutdown."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
