"""Persistent application setting accessors."""

from cogs.casino import bank as _bank

def get_setting(key, default=None):
    """Read a non-sensitive application setting without performing schema writes."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("setting key must be a non-empty string")
    with _bank._lock:
        row = _bank._connect().execute(
            "SELECT setting_value FROM app_settings WHERE setting_key=?", (key.strip(),)
        ).fetchone()
        return row["setting_value"] if row is not None else default


def set_setting(key, value):
    """Atomically persist a small application setting in the existing SQLite database."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("setting key must be a non-empty string")
    value = str(value)
    with _bank._lock:
        connection = _bank._connect()
        connection.execute(
            "INSERT INTO app_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(setting_key) DO UPDATE SET "
            "setting_value=excluded.setting_value, updated_at=excluded.updated_at",
            (key.strip(), value, time.time()),
        )
        connection.commit()
