# cogs/casino/bank.py
"""SQLite bank: balances, daily, streaks, inventory, whitelist. Thread-safe + atomic."""
import os
import sqlite3
import threading
import time

DB_FILE = "data/bank.db"
_lock = threading.RLock()
_conn = None

def _connect():
    global _conn
    if _conn is None:
        os.makedirs("data", exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0, last_daily REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0)")
        _conn.execute("CREATE TABLE IF NOT EXISTS inventory (user_id TEXT NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, item))")
        _conn.execute("CREATE TABLE IF NOT EXISTS whitelist (kind TEXT NOT NULL, target_id INTEGER NOT NULL, PRIMARY KEY (kind, target_id))")
        _conn.commit()
    return _conn

def _ensure(uid):
    c = _connect()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (str(uid),))
    c.commit()

def fmt(n):
    try:
        return format(int(n), ",d")
    except Exception:
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
    uid = str(user_id)
    with _lock:
        _ensure(uid)
        c = _connect()
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (int(amount), uid))
        c.commit()
        row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(row["balance"]) if row else 0

def set_balance(user_id, amount):
    uid = str(user_id)
    with _lock:
        _ensure(uid)
        _connect().execute("UPDATE users SET balance=? WHERE user_id=?", (int(amount), uid))
        _connect().commit()

def get_daily(uid):
    uid = str(uid)
    with _lock:
        _ensure(uid)
        row = _connect().execute("SELECT last_daily, streak FROM users WHERE user_id=?", (uid,)).fetchone()
        return float(row["last_daily"]), int(row["streak"])

def set_daily(uid, last, streak):
    uid = str(uid)
    with _lock:
        _ensure(uid)
        _connect().execute("UPDATE users SET last_daily=?, streak=? WHERE user_id=?", (float(last), int(streak), uid))
        _connect().commit()

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
    uid = str(user_id)
    with _lock:
        _ensure(uid)
        row = _connect().execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (uid, item)).fetchone()
        if row is None:
            _connect().execute("INSERT INTO inventory (user_id, item, qty) VALUES (?,?,?)", (uid, item, int(amount)))
        else:
            _connect().execute("UPDATE inventory SET qty = qty + ? WHERE user_id=? AND item=?", (int(amount), uid, item))
        _connect().commit()

def remove_item(user_id, item, amount=1):
    uid = str(user_id)
    with _lock:
        row = _connect().execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (uid, item)).fetchone()
        if not row or int(row["qty"]) < int(amount):
            return False
        _connect().execute("UPDATE inventory SET qty = qty - ? WHERE user_id=? AND item=?", (int(amount), uid, item))
        _connect().commit()
        return True

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

def reset_all():
    with _lock:
        c = _connect()
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM inventory")
        c.commit()

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


