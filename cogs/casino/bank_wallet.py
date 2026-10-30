"""Wallet administration, daily rewards, and inventory transactions."""

import math

from cogs.casino import bank as _bank

def add_guild_admin(guild_id, user_id):
    with _bank._lock:
        c = _bank._connect()
        changed = c.execute(
            "INSERT OR IGNORE INTO guild_admins (guild_id, user_id) VALUES (?, ?)",
            (str(guild_id), str(user_id)),
        ).rowcount
        c.commit()
        return changed == 1


def remove_guild_admin(guild_id, user_id):
    with _bank._lock:
        c = _bank._connect()
        changed = c.execute(
            "DELETE FROM guild_admins WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ).rowcount
        c.commit()
        return changed == 1


def list_guild_admins(guild_id):
    with _bank._lock:
        rows = _bank._connect().execute(
            "SELECT user_id FROM guild_admins WHERE guild_id=? ORDER BY user_id",
            (str(guild_id),),
        ).fetchall()
        return [int(row["user_id"]) for row in rows]


def is_guild_admin(guild_id, user_id):
    with _bank._lock:
        row = _bank._connect().execute(
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
        return False, _bank.get_balance(uid)

    item = item.strip()
    sqlite_max_int = _bank.SQLITE_MAX_INT
    if (
        quantity <= 0
        or unit_price < 0
        or quantity > sqlite_max_int
        or unit_price > sqlite_max_int
        or (unit_price and quantity > sqlite_max_int // unit_price)
    ):
        return False, _bank.get_balance(uid)
    total = quantity * unit_price
    with _bank._lock:
        c = _bank._connect()
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
            _bank._record_ledger(
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
    sqlite_max_int = _bank.SQLITE_MAX_INT
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
    if not 0 <= base_reward <= _bank.SQLITE_MAX_INT:
        raise ValueError("base_reward must be a non-negative SQLite-safe integer")

    with _bank._lock:
        c = _bank._connect()
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
            _bank._record_ledger(
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
        or reward > _bank.SQLITE_MAX_INT
    ):
        return False, _bank.get_balance(uid)

    item = item.strip()
    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            balance_row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = int(balance_row["balance"]) if balance_row else 0
            if balance > _bank.SQLITE_MAX_INT - reward:
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
            _bank._record_ledger(
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


def charge_up_to(user_id, amount):
    """Charge at most the available integer balance; return (charged, new_balance)."""
    uid = str(user_id)
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > _bank.SQLITE_MAX_INT:
        return 0, _bank.get_balance(uid)
    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            balance = max(0, int(row["balance"])) if row else 0
            charged = min(balance, amount)
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (charged, uid))
            balance_after = balance - charged
            if charged:
                _bank._record_ledger(
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
    if amount < 0 or amount > _bank.SQLITE_MAX_INT:
        raise ValueError("balance must be within SQLite's non-negative integer range")

    uid = str(user_id)
    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            previous_balance = int(row["balance"]) if row else 0
            c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, uid))
            _bank._record_ledger(
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
    with _bank._lock:
        _bank._ensure(uid)
        row = _bank._connect().execute("SELECT last_daily, streak FROM users WHERE user_id=?", (uid,)).fetchone()
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
    if streak > _bank.SQLITE_MAX_INT:
        raise ValueError("Daily streak exceeds SQLite's integer range")

    uid = str(uid)
    with _bank._lock:
        c = _bank._connect()
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
    with _bank._lock:
        row = _bank._connect().execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (str(user_id), item)).fetchone()
        return int(row["qty"]) if row else 0

def get_inventory(user_id):
    with _bank._lock:
        rows = _bank._connect().execute("SELECT item, qty FROM inventory WHERE user_id=?", (str(user_id),)).fetchall()
        return {r["item"]: int(r["qty"]) for r in rows}

def top_balances(limit=10):
    with _bank._lock:
        rows = _bank._connect().execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT ?", (int(limit),)).fetchall()
        return [(r["user_id"], int(r["balance"])) for r in rows]

def add_item(user_id, item, amount=1):
    """Add a strictly positive quantity without overflowing SQLite's integer range."""
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= _bank.SQLITE_MAX_INT:
        raise ValueError("item quantity must be a positive SQLite-safe integer")
    if not isinstance(item, str) or not item.strip():
        raise ValueError("item name is required")

    uid = str(user_id)
    with _bank._lock:
        c = _bank._connect()
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
                if current < 0 or current > _bank.SQLITE_MAX_INT - amount:
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
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= _bank.SQLITE_MAX_INT:
        return False
    if not isinstance(item, str) or not item.strip():
        return False

    uid = str(user_id)
    with _bank._lock:
        c = _bank._connect()
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
    with _bank._lock:
        rows = _bank._connect().execute("SELECT kind, target_id FROM whitelist").fetchall()
        out = {"users": [], "roles": []}
        for r in rows:
            if r["kind"] in out:
                out[r["kind"]].append(int(r["target_id"]))
        return out

def whitelist_add(kind, target_id):
    with _bank._lock:
        _bank._connect().execute("INSERT OR IGNORE INTO whitelist (kind, target_id) VALUES (?,?)", (str(kind), int(target_id)))
        _bank._connect().commit()

def whitelist_remove(kind, target_id):
    with _bank._lock:
        _bank._connect().execute("DELETE FROM whitelist WHERE kind=? AND target_id=?", (str(kind), int(target_id)))
        _bank._connect().commit()
