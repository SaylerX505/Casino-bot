# --- SQLITE BANK ---
"""SQLite bank: balances, daily, streaks, inventory, whitelist. Thread-safe + atomic."""
import math
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
    """Charge coins and add inventory atomically, guarding both integer bounds."""
    uid = str(user_id)
    if (
        not isinstance(item, str)
        or not item.strip()
        or isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or isinstance(unit_price, bool)
        or not isinstance(unit_price, int)
    ):
        return False, get_balance(uid)

    item = item.strip()
    sqlite_max_int = SQLITE_MAX_INT
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
            inventory_row = c.execute(
                "SELECT qty FROM inventory WHERE user_id=? AND item=?",
                (uid, item),
            ).fetchone()
            current_qty = int(inventory_row["qty"]) if inventory_row else 0
            if current_qty < 0 or current_qty > sqlite_max_int - quantity:
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                balance = int(row["balance"]) if row else 0
                c.rollback()
                return False, balance

            changed = c.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
                (total, uid, total),
            ).rowcount
            if changed != 1:
                row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                balance = int(row["balance"]) if row else 0
                c.rollback()
                return False, balance
            if inventory_row is None:
                c.execute(
                    "INSERT INTO inventory (user_id,item,qty) VALUES (?,?,?)",
                    (uid, item, quantity),
                )
            else:
                c.execute(
                    "UPDATE inventory SET qty=qty+? WHERE user_id=? AND item=?",
                    (quantity, uid, item),
                )
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance_after = int(row["balance"]) if row else 0
            _record_ledger(
                c,
                event_type="shop_purchase",
                user_id=uid,
                delta=-total,
                balance_after=balance_after,
                metadata={"item": item, "quantity": quantity, "unit_price": unit_price},
            )
            c.commit()
            return True, balance_after
        except Exception:
            c.rollback()
            raise

def claim_daily(user_id, now_ts, base_reward):
    """Atomically enforce cooldown, update streak, and grant a validated reward."""
    uid = str(user_id)
    sqlite_max_int = SQLITE_MAX_INT
    if isinstance(now_ts, bool) or not isinstance(now_ts, (int, float)):
        raise TypeError("now_ts must be a numeric timestamp")
    try:
        now_ts = float(now_ts)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("now_ts must be finite and non-negative") from None
    if not math.isfinite(now_ts) or now_ts < 0:
        raise ValueError("now_ts must be finite and non-negative")
    if isinstance(base_reward, bool) or not isinstance(base_reward, int):
        raise TypeError("base_reward must be an integer")
    if not 0 <= base_reward <= SQLITE_MAX_INT:
        raise ValueError("base_reward must be a non-negative SQLite-safe integer")

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
            if balance > sqlite_max_int - reward:
                c.rollback()
                return {"claimed": False, "reason": "balance_limit", "last_daily": last,
                        "streak": streak, "amount": 0, "balance": balance}
            new_streak = streak + 1
            c.execute(
                "UPDATE users SET balance=balance+?,last_daily=?,streak=? WHERE user_id=?",
                (reward, now_ts, new_streak, uid),
            )
            balance_after = balance + reward
            _record_ledger(
                c,
                event_type="daily_reward",
                user_id=uid,
                delta=reward,
                balance_after=balance_after,
                metadata={"base_reward": base_reward, "streak": new_streak},
            )
            c.commit()
            return {"claimed": True, "last_daily": now_ts, "streak": new_streak,
                    "amount": reward, "balance": balance_after}
        except Exception:
            c.rollback()
            raise

def redeem_item(user_id, item, reward):
    """Consume one inventory item and grant its reward atomically."""
    uid = str(user_id)
    if (
        not isinstance(item, str)
        or not item.strip()
        or isinstance(reward, bool)
        or not isinstance(reward, int)
        or reward < 0
        or reward > SQLITE_MAX_INT
    ):
        return False, get_balance(uid)

    item = item.strip()
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            balance_row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = int(balance_row["balance"]) if balance_row else 0
            if balance > SQLITE_MAX_INT - reward:
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
            balance_after = balance + reward
            _record_ledger(
                c,
                event_type="consumable_redemption",
                user_id=uid,
                delta=reward,
                balance_after=balance_after,
                metadata={"item": item},
            )
            c.commit()
            return True, balance_after
        except Exception:
            c.rollback()
            raise

def place_bet(player_a, player_b, amount):
    """Collect both stakes atomically; neither player is charged on failure."""
    a, b = str(player_a), str(player_b)
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount <= 0
        or amount > SQLITE_MAX_INT
        or a == b
    ):
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
            transaction_id = uuid.uuid4().hex
            _record_ledger(
                c,
                event_type="pvp_bet_stake",
                user_id=a,
                delta=-amount,
                balance_after=int(row_a["balance"]) - amount,
                transaction_id=transaction_id,
                metadata={"counterparty": b, "stake": amount},
            )
            _record_ledger(
                c,
                event_type="pvp_bet_stake",
                user_id=b,
                delta=-amount,
                balance_after=int(row_b["balance"]) - amount,
                transaction_id=transaction_id,
                metadata={"counterparty": a, "stake": amount},
            )
            c.commit()
            return True
        except Exception:
            c.rollback()
            raise
def create_rps_challenge(game_id, player_a, player_b, amount, *, channel_id=None):
    """Create a challenge invitation without reserving either player's balance."""
    game_id, a, b = str(game_id).strip(), str(player_a).strip(), str(player_b).strip()
    if (
        not game_id
        or not a
        or not b
        or a == b
        or isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount <= 0
        or amount > SQLITE_MAX_INT // 2
    ):
        return False
    if channel_id is not None and (
        isinstance(channel_id, bool)
        or not isinstance(channel_id, int)
        or not 0 < channel_id <= SQLITE_MAX_INT
    ):
        raise ValueError("channel_id must be a positive integer")

    timestamp = now()
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "INSERT INTO rps_challenges "
                "(game_id,player_a,player_b,amount,status,created_at,updated_at,channel_id) "
                "VALUES (?,?,?,?,'pending',?,?,?)",
                (game_id, a, b, amount, timestamp, timestamp, channel_id),
            )
            c.commit()
            return True
        except Exception:
            c.rollback()
            raise


def set_rps_challenge_message_reference(game_id, channel_id, message_id):
    """Persist the challenge panel for clean shutdown/restart behavior."""
    game_id = str(game_id).strip()
    if not game_id:
        return False
    for field_name, field_value in (("channel_id", channel_id), ("message_id", message_id)):
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 < field_value <= SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer")

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE rps_challenges SET channel_id=?,message_id=?,updated_at=? "
                "WHERE game_id=? AND status IN ('pending','accepted')",
                (channel_id, message_id, now(), game_id),
            ).rowcount
            if changed == 1:
                # If acceptance raced with the command's message-reference write,
                # update the escrow record too so startup can always close it.
                c.execute(
                    "UPDATE rps_wagers SET channel_id=?,message_id=? "
                    "WHERE game_id=? AND status='active'",
                    (channel_id, message_id, game_id),
                )
            c.commit()
            return changed == 1
        except Exception:
            c.rollback()
            raise


def rps_challenge_status(game_id):
    """Read the durable invite state without touching player balances."""
    with _lock:
        row = _connect().execute(
            "SELECT game_id,player_a,player_b,amount,status,outcome,channel_id,message_id "
            "FROM rps_challenges WHERE game_id=?",
            (str(game_id),),
        ).fetchone()
        return dict(row) if row is not None else None


def accept_rps_challenge(game_id):
    """Atomically accept an invitation and escrow both stakes exactly once."""
    game_id = str(game_id).strip()
    if not game_id:
        return {"accepted": False, "reason": "not_found"}

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            challenge = c.execute(
                "SELECT player_a,player_b,amount,status,channel_id,message_id "
                "FROM rps_challenges WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if challenge is None:
                c.rollback()
                return {"accepted": False, "reason": "not_found"}
            status = str(challenge["status"])
            if status == "accepted":
                c.rollback()
                return {"accepted": True, "already_accepted": True, "status": status}
            if status != "pending":
                c.rollback()
                return {"accepted": False, "reason": status}

            a, b = str(challenge["player_a"]), str(challenge["player_b"])
            amount = int(challenge["amount"])
            if c.execute("SELECT 1 FROM rps_wagers WHERE game_id=?", (game_id,)).fetchone():
                c.execute(
                    "UPDATE rps_challenges SET status='cancelled',outcome='duplicate_wager',updated_at=? "
                    "WHERE game_id=? AND status='pending'",
                    (now(), game_id),
                )
                c.commit()
                return {"accepted": False, "reason": "duplicate_wager"}

            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (a,))
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (b,))
            row_a = c.execute("SELECT balance FROM users WHERE user_id=?", (a,)).fetchone()
            row_b = c.execute("SELECT balance FROM users WHERE user_id=?", (b,)).fetchone()
            balance_a = int(row_a["balance"]) if row_a else 0
            balance_b = int(row_b["balance"]) if row_b else 0
            if balance_a < amount or balance_b < amount:
                c.execute(
                    "UPDATE rps_challenges SET status='cancelled',outcome='insufficient_funds',updated_at=? "
                    "WHERE game_id=? AND status='pending'",
                    (now(), game_id),
                )
                c.commit()
                return {
                    "accepted": False,
                    "reason": "insufficient_funds",
                    "player_a_balance": balance_a,
                    "player_b_balance": balance_b,
                    "amount": amount,
                }

            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, a))
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, b))
            transaction_id = uuid.uuid4().hex
            _record_ledger(
                c,
                event_type="rps_wager_stake",
                user_id=a,
                delta=-amount,
                balance_after=balance_a - amount,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={"counterparty": b, "stake": amount},
            )
            _record_ledger(
                c,
                event_type="rps_wager_stake",
                user_id=b,
                delta=-amount,
                balance_after=balance_b - amount,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={"counterparty": a, "stake": amount},
            )
            c.execute(
                "INSERT INTO rps_wagers "
                "(game_id,player_a,player_b,amount,status,created_at,channel_id,message_id) "
                "VALUES (?,?,?,?,'active',?,?,?)",
                (
                    game_id,
                    a,
                    b,
                    amount,
                    now(),
                    challenge["channel_id"],
                    challenge["message_id"],
                ),
            )
            c.execute(
                "UPDATE rps_challenges SET status='accepted',outcome=NULL,updated_at=? "
                "WHERE game_id=? AND status='pending'",
                (now(), game_id),
            )
            c.commit()
            return {
                "accepted": True,
                "already_accepted": False,
                "status": "accepted",
                "amount": amount,
                "player_a_balance": balance_a - amount,
                "player_b_balance": balance_b - amount,
            }
        except Exception:
            c.rollback()
            raise


def decline_rps_challenge(game_id, user_id):
    """Decline a pending challenge only when called by its invited opponent."""
    return _close_rps_challenge(game_id, user_id, allowed_role="opponent", outcome="declined")


def cancel_rps_challenge(game_id, user_id):
    """Cancel an unaccepted challenge only when called by its original challenger."""
    return _close_rps_challenge(game_id, user_id, allowed_role="challenger", outcome="cancelled")


def _close_rps_challenge(game_id, user_id, *, allowed_role, outcome):
    game_id, uid = str(game_id).strip(), str(user_id).strip()
    if not game_id or not uid:
        return {"closed": False, "reason": "not_found"}
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            challenge = c.execute(
                "SELECT player_a,player_b,status FROM rps_challenges WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if challenge is None:
                c.rollback()
                return {"closed": False, "reason": "not_found"}
            allowed_user = str(challenge["player_b"] if allowed_role == "opponent" else challenge["player_a"])
            if uid != allowed_user:
                c.rollback()
                return {"closed": False, "reason": "not_authorized"}
            if str(challenge["status"]) != "pending":
                c.rollback()
                return {"closed": False, "reason": str(challenge["status"])}
            c.execute(
                "UPDATE rps_challenges SET status=?,outcome=?,updated_at=? "
                "WHERE game_id=? AND status='pending'",
                (outcome, outcome, now(), game_id),
            )
            c.commit()
            return {"closed": True, "reason": None, "status": outcome}
        except Exception:
            c.rollback()
            raise


def expire_rps_challenge(game_id):
    """Expire a pending invite; it never transfers or refunds coins because none were reserved."""
    game_id = str(game_id).strip()
    if not game_id:
        return False
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE rps_challenges SET status='expired',outcome='timeout',updated_at=? "
                "WHERE game_id=? AND status='pending'",
                (now(), game_id),
            ).rowcount
            c.commit()
            return changed == 1
        except Exception:
            c.rollback()
            raise


def expire_pending_rps_challenges():
    """Close every pre-acceptance invite at startup and return panels needing cleanup."""
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT game_id,player_a,player_b,amount,channel_id,message_id "
                "FROM rps_challenges WHERE status='pending' ORDER BY created_at"
            ).fetchall()
            records = [dict(row) for row in rows]
            timestamp = now()
            c.execute(
                "UPDATE rps_challenges SET status='expired',outcome='restart',updated_at=? "
                "WHERE status='pending'",
                (timestamp,),
            )
            c.commit()
            return records
        except Exception:
            c.rollback()
            raise


def create_rps_wager(game_id, player_a, player_b, amount):
    """Atomically debit both integer stakes and persist a recoverable wager."""
    game_id, a, b = str(game_id), str(player_a), str(player_b)
    if (
        not game_id
        or a == b
        or isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount <= 0
        or amount > SQLITE_MAX_INT
    ):
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
            transaction_id = uuid.uuid4().hex
            _record_ledger(
                c,
                event_type="rps_wager_stake",
                user_id=a,
                delta=-amount,
                balance_after=int(row_a["balance"]) - amount,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={"counterparty": b, "stake": amount},
            )
            _record_ledger(
                c,
                event_type="rps_wager_stake",
                user_id=b,
                delta=-amount,
                balance_after=int(row_b["balance"]) - amount,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={"counterparty": a, "stake": amount},
            )
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
    sqlite_max_int = SQLITE_MAX_INT
    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            wager = c.execute(
                "SELECT player_a,player_b,amount,status,outcome FROM rps_wagers WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if wager is None:
                c.rollback()
                return {"settled": False, "reason": "not_found"}
            if wager["status"] != "active":
                c.rollback()
                status = str(wager["status"])
                amount = int(wager["amount"])
                outcome = str(wager["outcome"] or "")
                settled_winner = (
                    outcome.partition(":")[2]
                    if status == "settled" and outcome.startswith("winner:")
                    else None
                )
                payout = 0
                tax = 0
                if settled_winner is not None:
                    pot = amount * 2
                    payout = (pot * 95) // 100
                    tax = pot - payout
                return {
                    "settled": True,
                    "already_settled": True,
                    "status": status,
                    "reason": None,
                    "winner_id": settled_winner,
                    "payout": payout,
                    "tax": tax,
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
            settlement_transaction_id = uuid.uuid4().hex

            def refund_stakes():
                if balances[a] > sqlite_max_int - amount or balances[b] > sqlite_max_int - amount:
                    return False
                c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, a))
                c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, b))
                _record_ledger(
                    c,
                    event_type="rps_wager_refund",
                    user_id=a,
                    delta=amount,
                    balance_after=balances[a] + amount,
                    transaction_id=settlement_transaction_id,
                    reference=game_id,
                    metadata={"stake": amount},
                )
                _record_ledger(
                    c,
                    event_type="rps_wager_refund",
                    user_id=b,
                    delta=amount,
                    balance_after=balances[b] + amount,
                    transaction_id=settlement_transaction_id,
                    reference=game_id,
                    metadata={"stake": amount},
                )
                return True

            if winner_id is not None:
                winner = str(winner_id)
                pot = amount * 2
                payout = (pot * 95) // 100
                tax = pot - payout
                if balances[winner] <= sqlite_max_int - payout:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (payout, winner))
                    _record_ledger(
                        c,
                        event_type="rps_wager_payout",
                        user_id=winner,
                        delta=payout,
                        balance_after=balances[winner] + payout,
                        transaction_id=settlement_transaction_id,
                        reference=game_id,
                        metadata={"stake": amount, "pot": pot, "tax": tax},
                    )
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



def create_casino_wager(
    game_id,
    user_id,
    game_type,
    amount,
    choice,
    result,
    won,
    win_payout,
    *,
    channel_id=None,
    message_id=None,
):
    """Atomically debit one player's stake and persist the complete game outcome."""
    game_id = str(game_id).strip()
    uid = str(user_id).strip()
    game_type = str(game_type).strip()
    choice = str(choice).strip()
    result = str(result).strip()
    if not game_id or not uid or not game_type or not choice or not result:
        raise ValueError("casino wager identifiers and outcome fields are required")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= SQLITE_MAX_INT:
        raise ValueError("casino wager stake must be a positive SQLite-safe integer")
    if not isinstance(won, bool):
        raise TypeError("casino wager won flag must be a boolean")
    if (
        isinstance(win_payout, bool)
        or not isinstance(win_payout, int)
        or not 0 <= win_payout <= SQLITE_MAX_INT
    ):
        raise ValueError("casino wager payout must be a non-negative SQLite-safe integer")
    if won and win_payout < amount:
        raise ValueError("winning casino wager gross payout cannot be smaller than its stake")
    if not won and win_payout != 0:
        raise ValueError("losing casino wagers cannot carry a payout")
    for field_name, field_value in (("channel_id", channel_id), ("message_id", message_id)):
        if field_value is not None and (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 < field_value <= SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer when provided")

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM casino_wagers WHERE game_id=?", (game_id,)).fetchone():
                c.rollback()
                return {"created": False, "reason": "duplicate_game"}

            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = int(row["balance"]) if row is not None else 0
            if balance < amount:
                c.rollback()
                return {"created": False, "reason": "insufficient_funds", "balance": balance}

            balance_after = balance - amount
            c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
            transaction_id = uuid.uuid4().hex
            _record_ledger(
                c,
                event_type="casino_wager_stake",
                user_id=uid,
                delta=-amount,
                balance_after=balance_after,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={
                    "game_type": game_type,
                    "stake": amount,
                    "choice": choice,
                    "result": result,
                    "won": won,
                    "win_payout": win_payout,
                },
            )
            c.execute(
                "INSERT INTO casino_wagers "
                "(game_id,user_id,game_type,stake,choice,result,won,win_payout,"
                "status,outcome,payout,created_at,channel_id,message_id) "
                "VALUES (?,?,?,?,?,?,?,?,'active',NULL,0,?,?,?)",
                (
                    game_id,
                    uid,
                    game_type,
                    amount,
                    choice,
                    result,
                    int(won),
                    win_payout,
                    now(),
                    channel_id,
                    message_id,
                ),
            )
            c.commit()
            return {"created": True, "balance_after": balance_after}
        except Exception:
            c.rollback()
            raise



def play_casino_wager(
    game_id,
    user_id,
    game_type,
    amount,
    choice,
    result,
    won,
    win_payout,
    *,
    channel_id=None,
    message_id=None,
):
    """Create and settle a single-step wager in one transaction.

    Unlike create_casino_wager(), this API never commits an active wager. The
    stake, final outcome, balance receipt, wager row, and ledger entries either
    commit together or do not happen. Multi-step games such as Blackjack should
    continue to use create_casino_wager() followed by settle_casino_wager().
    """
    game_id = str(game_id).strip()
    uid = str(user_id).strip()
    game_type = str(game_type).strip()
    choice = str(choice).strip()
    result = str(result).strip()
    if not game_id or not uid or not game_type or not choice or not result:
        raise ValueError("casino wager identifiers and outcome fields are required")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= SQLITE_MAX_INT:
        raise ValueError("casino wager stake must be a positive SQLite-safe integer")
    if not isinstance(won, bool):
        raise TypeError("casino wager won flag must be a boolean")
    if (
        isinstance(win_payout, bool)
        or not isinstance(win_payout, int)
        or not 0 <= win_payout <= SQLITE_MAX_INT
    ):
        raise ValueError("casino wager payout must be a non-negative SQLite-safe integer")
    if won and win_payout < amount:
        raise ValueError("winning casino wager gross payout cannot be smaller than its stake")
    if not won and win_payout != 0:
        raise ValueError("losing casino wagers cannot carry a payout")
    for field_name, field_value in (("channel_id", channel_id), ("message_id", message_id)):
        if field_value is not None and (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 < field_value <= SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer when provided")

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            if c.execute(
                "SELECT 1 FROM casino_wagers WHERE game_id=?", (game_id,)
            ).fetchone():
                c.rollback()
                return {"created": False, "reason": "duplicate_game"}

            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = int(row["balance"]) if row is not None else 0
            if balance < amount:
                c.rollback()
                return {
                    "created": False,
                    "reason": "insufficient_funds",
                    "balance": balance,
                }

            balance_after_stake = balance - amount
            transaction_id = uuid.uuid4().hex
            c.execute(
                "UPDATE users SET balance=? WHERE user_id=?",
                (balance_after_stake, uid),
            )
            _record_ledger(
                c,
                event_type="casino_wager_stake",
                user_id=uid,
                delta=-amount,
                balance_after=balance_after_stake,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={
                    "game_type": game_type,
                    "stake": amount,
                    "choice": choice,
                    "result": result,
                    "won": won,
                    "win_payout": win_payout,
                    "settlement_mode": "atomic",
                },
            )

            reason = None
            if won and balance_after_stake > SQLITE_MAX_INT - win_payout:
                # Do not partially settle a winning round when the wallet is full.
                balance_after = balance
                c.execute(
                    "UPDATE users SET balance=? WHERE user_id=?",
                    (balance_after, uid),
                )
                _record_ledger(
                    c,
                    event_type="casino_wager_refund",
                    user_id=uid,
                    delta=amount,
                    balance_after=balance_after,
                    transaction_id=transaction_id,
                    reference=game_id,
                    metadata={
                        "game_type": game_type,
                        "stake": amount,
                        "reason": "payout_balance_limit",
                    },
                )
                status, outcome, payout = "refunded", "balance_limit", 0
                reason = "balance_limit"
            elif won:
                payout = win_payout
                balance_after = balance_after_stake + payout
                c.execute(
                    "UPDATE users SET balance=? WHERE user_id=?",
                    (balance_after, uid),
                )
                _record_ledger(
                    c,
                    event_type="casino_wager_payout",
                    user_id=uid,
                    delta=payout,
                    balance_after=balance_after,
                    transaction_id=transaction_id,
                    reference=game_id,
                    metadata={
                        "game_type": game_type,
                        "stake": amount,
                        "gross_payout": payout,
                        "net_profit": payout - amount,
                        "settlement_mode": "atomic",
                    },
                )
                status, outcome = "settled", "win"
            else:
                payout = 0
                balance_after = balance_after_stake
                _record_ledger(
                    c,
                    event_type="casino_wager_loss",
                    user_id=uid,
                    delta=0,
                    balance_after=balance_after,
                    transaction_id=transaction_id,
                    reference=game_id,
                    metadata={
                        "game_type": game_type,
                        "stake": amount,
                        "settlement_mode": "atomic",
                    },
                )
                status, outcome = "settled", "loss"

            timestamp = now()
            c.execute(
                "INSERT INTO casino_wagers "
                "(game_id,user_id,game_type,stake,choice,result,won,win_payout,"
                "status,outcome,payout,created_at,settled_at,channel_id,message_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    game_id,
                    uid,
                    game_type,
                    amount,
                    choice,
                    result,
                    int(won),
                    win_payout,
                    status,
                    outcome,
                    payout,
                    timestamp,
                    timestamp,
                    channel_id,
                    message_id,
                ),
            )
            c.commit()
            return {
                "created": True,
                "settled": True,
                "already_settled": False,
                "status": status,
                "outcome": outcome,
                "reason": reason,
                "game_id": game_id,
                "user_id": uid,
                "game_type": game_type,
                "stake": amount,
                "choice": choice,
                "result": result,
                "won": won,
                "payout": payout,
                "balance_after": balance_after,
            }
        except Exception:
            c.rollback()
            raise


def settle_casino_wager(
    game_id,
    *,
    force_refund=False,
    final_result=None,
    final_won=None,
    final_payout=None,
    final_outcome=None,
):
    """Settle a persisted wager once; optionally commit a multi-step game's final result."""
    game_id = str(game_id)
    if not game_id:
        return {"settled": False, "reason": "not_found"}
    if not isinstance(force_refund, bool):
        raise TypeError("force_refund must be a boolean")

    final_fields = (final_result, final_won, final_payout)
    has_final_fields = any(value is not None for value in final_fields)
    if has_final_fields and not all(value is not None for value in final_fields):
        raise ValueError("final_result, final_won, and final_payout must be provided together")
    if not has_final_fields and final_outcome is not None:
        raise ValueError("final_outcome requires final settlement fields")
    if has_final_fields:
        if not isinstance(final_result, str) or not final_result.strip():
            raise ValueError("final result must be a non-empty string")
        if not isinstance(final_won, bool):
            raise TypeError("final won flag must be a boolean")
        if (
            isinstance(final_payout, bool)
            or not isinstance(final_payout, int)
            or not 0 <= final_payout <= SQLITE_MAX_INT
        ):
            raise ValueError("final payout must be a non-negative SQLite-safe integer")
        if final_won and final_payout <= 0:
            raise ValueError("a winning final result must have a positive gross payout")
        if not final_won and final_payout != 0:
            raise ValueError("a losing or push result must not carry a win payout")
        if final_outcome not in (None, "win", "loss", "push"):
            raise ValueError("unsupported final outcome")
        if final_outcome == "win" and not final_won:
            raise ValueError("win outcome requires final_won=True")
        if final_outcome in ("loss", "push") and final_won:
            raise ValueError("loss/push outcome requires final_won=False")

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            wager = c.execute(
                "SELECT game_id,user_id,game_type,stake,choice,result,won,win_payout,"
                "status,outcome,payout FROM casino_wagers WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if wager is None:
                c.rollback()
                return {"settled": False, "reason": "not_found"}

            def receipt(
                *,
                settled,
                already_settled,
                status,
                outcome,
                payout,
                reason=None,
                balance_after=None,
                result=None,
                won=None,
            ):
                return {
                    "settled": settled,
                    "already_settled": already_settled,
                    "status": status,
                    "outcome": outcome,
                    "reason": reason,
                    "game_id": str(wager["game_id"]),
                    "user_id": str(wager["user_id"]),
                    "game_type": str(wager["game_type"]),
                    "stake": int(wager["stake"]),
                    "choice": str(wager["choice"]),
                    "result": str(wager["result"]) if result is None else result,
                    "won": bool(wager["won"]) if won is None else won,
                    "payout": int(payout),
                    "balance_after": balance_after,
                }

            if wager["status"] != "active":
                status = str(wager["status"])
                outcome = str(wager["outcome"] or "")
                payout = int(wager["payout"] or 0)
                row = c.execute(
                    "SELECT balance FROM users WHERE user_id=?",
                    (str(wager["user_id"]),),
                ).fetchone()
                balance_after = int(row["balance"]) if row is not None else None
                c.rollback()
                return receipt(
                    settled=True,
                    already_settled=True,
                    status=status,
                    outcome=outcome,
                    payout=payout,
                    reason="balance_limit" if outcome == "balance_limit" else None,
                    balance_after=balance_after,
                )

            uid = str(wager["user_id"])
            stake = int(wager["stake"])
            won = bool(wager["won"]) if not has_final_fields else final_won
            win_payout = int(wager["win_payout"]) if not has_final_fields else final_payout
            result_text = str(wager["result"]) if not has_final_fields else final_result.strip()
            selected_outcome = final_outcome or ("win" if won else "loss")
            if won and win_payout < stake:
                c.rollback()
                raise ValueError("gross payout cannot be smaller than the original stake")

            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            if row is None:
                c.rollback()
                return {"settled": False, "reason": "wallet_missing"}
            balance = int(row["balance"])
            transaction_id = uuid.uuid4().hex

            if force_refund:
                if balance > SQLITE_MAX_INT - stake:
                    c.rollback()
                    return {"settled": False, "reason": "balance_limit"}
                balance_after = balance + stake
                c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                _record_ledger(
                    c, event_type="casino_wager_refund", user_id=uid, delta=stake,
                    balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                    metadata={"game_type": str(wager["game_type"]), "stake": stake, "reason": "interrupted"},
                )
                status, outcome, payout = "refunded", "refund", 0
            elif selected_outcome == "push":
                if balance > SQLITE_MAX_INT - stake:
                    c.rollback()
                    return {"settled": False, "reason": "balance_limit"}
                balance_after = balance + stake
                c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                _record_ledger(
                    c, event_type="casino_wager_push", user_id=uid, delta=stake,
                    balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                    metadata={"game_type": str(wager["game_type"]), "stake": stake, "gross_return": stake},
                )
                status, outcome, payout = "settled", "push", stake
            elif won:
                if balance <= SQLITE_MAX_INT - win_payout:
                    balance_after = balance + win_payout
                    c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                    _record_ledger(
                        c, event_type="casino_wager_payout", user_id=uid, delta=win_payout,
                        balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                        metadata={
                            "game_type": str(wager["game_type"]),
                            "stake": stake,
                            "gross_payout": win_payout,
                            "net_profit": win_payout - stake,
                        },
                    )
                    status, outcome, payout = "settled", "win", win_payout
                else:
                    if balance > SQLITE_MAX_INT - stake:
                        c.rollback()
                        return {"settled": False, "reason": "balance_limit"}
                    balance_after = balance + stake
                    c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                    _record_ledger(
                        c, event_type="casino_wager_refund", user_id=uid, delta=stake,
                        balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                        metadata={"game_type": str(wager["game_type"]), "stake": stake, "reason": "payout_balance_limit"},
                    )
                    status, outcome, payout = "refunded", "balance_limit", 0
            else:
                balance_after = balance
                _record_ledger(
                    c, event_type="casino_wager_loss", user_id=uid, delta=0,
                    balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                    metadata={"game_type": str(wager["game_type"]), "stake": stake},
                )
                status, outcome, payout = "settled", "loss", 0

            c.execute(
                "UPDATE casino_wagers SET result=?,won=?,win_payout=?,status=?,outcome=?,"
                "payout=?,settled_at=? WHERE game_id=? AND status='active'",
                (
                    result_text,
                    int(won),
                    win_payout,
                    status,
                    outcome,
                    payout,
                    now(),
                    game_id,
                ),
            )
            c.commit()
            return receipt(
                settled=True,
                already_settled=False,
                status=status,
                outcome=outcome,
                payout=payout,
                reason="balance_limit" if outcome == "balance_limit" else None,
                balance_after=balance_after,
                result=result_text,
                won=won,
            )
        except Exception:
            c.rollback()
            raise

def refund_orphaned_casino_wagers():
    """Refund unfinished solo wagers during startup/recovery without blocking other records."""
    import logging

    logger = logging.getLogger(__name__)
    with _lock:
        rows = _connect().execute(
            "SELECT game_id FROM casino_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        game_ids = [str(row["game_id"]) for row in rows]

    refunded = 0
    pending = 0
    for game_id in game_ids:
        try:
            result = settle_casino_wager(game_id, force_refund=True)
        except Exception:
            pending += 1
            logger.exception("Could not refund interrupted casino wager game_id=%s; escrow remains recorded", game_id)
            continue
        if not result.get("settled"):
            pending += 1
            logger.error("Interrupted casino wager remains active game_id=%s reason=%s", game_id, result.get("reason", "unknown"))
        elif result.get("status") == "refunded":
            refunded += 1

    if pending:
        logger.error("%s interrupted solo casino wager(s) remain active and require retry or operator reconciliation", pending)
    return refunded


def active_casino_wager_ids():
    """Return active casino wager IDs in stable order for recovery."""
    with _lock:
        rows = _connect().execute(
            "SELECT game_id FROM casino_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [str(row["game_id"]) for row in rows]


def active_casino_wager_records():
    """Return durable panel references for wagers that may need startup recovery."""
    with _lock:
        rows = _connect().execute(
            "SELECT game_id,user_id,game_type,stake,channel_id,message_id "
            "FROM casino_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def casino_wager_status(game_id):
    """Return the durable status and outcome needed to verify a recovery receipt."""
    with _lock:
        row = _connect().execute(
            "SELECT status,outcome,stake,game_type FROM casino_wagers WHERE game_id=?",
            (str(game_id),),
        ).fetchone()
        return dict(row) if row is not None else None


def set_rps_wager_message_reference(game_id, channel_id, message_id):
    """Attach the public challenge panel IDs to an active wager for restart cleanup."""
    game_id = str(game_id).strip()
    if not game_id:
        return False
    for field_name, field_value in (("channel_id", channel_id), ("message_id", message_id)):
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 < field_value <= SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer")

    with _lock:
        c = _connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE rps_wagers SET channel_id=?,message_id=? "
                "WHERE game_id=? AND status='active'",
                (channel_id, message_id, game_id),
            ).rowcount
            c.commit()
            return changed == 1
        except Exception:
            c.rollback()
            raise


def active_rps_wager_records():
    """Return active escrow and Discord panel references before startup refund."""
    with _lock:
        rows = _connect().execute(
            "SELECT game_id,player_a,player_b,amount,channel_id,message_id "
            "FROM rps_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def rps_wager_status(game_id):
    """Read a durable RPS receipt for recovery decisions without changing balances."""
    with _lock:
        row = _connect().execute(
            "SELECT game_id,player_a,player_b,amount,status,outcome,channel_id,message_id "
            "FROM rps_wagers WHERE game_id=?",
            (str(game_id),),
        ).fetchone()
        return dict(row) if row is not None else None


def charge_up_to(user_id, amount):
    """Charge at most the available integer balance; return (charged, new_balance)."""
    uid = str(user_id)
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > SQLITE_MAX_INT:
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
            balance_after = balance - charged
            if charged:
                _record_ledger(
                    c,
                    event_type="balance_charge",
                    user_id=uid,
                    delta=-charged,
                    balance_after=balance_after,
                    metadata={"requested_amount": amount},
                )
            c.commit()
            return charged, balance_after
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
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            previous_balance = int(row["balance"]) if row else 0
            c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, uid))
            _record_ledger(
                c,
                event_type="balance_set",
                user_id=uid,
                delta=amount - previous_balance,
                balance_after=amount,
                metadata={"previous_balance": previous_balance},
            )
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
def get_setting(key, default=None):
    """Read a non-sensitive application setting without performing schema writes."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("setting key must be a non-empty string")
    with _lock:
        row = _connect().execute(
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
