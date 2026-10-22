"""Append-only economy ledger helpers; transaction boundaries remain in the bank layer."""

import json
import time
import uuid

SQLITE_MAX_INT = (1 << 63) - 1


def record_economy_event(
    connection,
    *,
    event_type,
    user_id,
    delta,
    balance_after,
    transaction_id=None,
    reference=None,
    metadata=None,
):
    """Append an economy event on the caller's open transaction; never commits."""
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("ledger event_type must be non-empty")
    if isinstance(delta, bool) or not isinstance(delta, int):
        raise TypeError("ledger delta must be an integer")
    if (
        isinstance(balance_after, bool)
        or not isinstance(balance_after, int)
        or balance_after < 0
        or balance_after > SQLITE_MAX_INT
    ):
        raise ValueError("ledger balance_after must be a non-negative SQLite-safe integer")
    metadata_json = (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if metadata is not None
        else None
    )
    txid = str(transaction_id or uuid.uuid4().hex)
    connection.execute(
        "INSERT INTO economy_ledger "
        "(transaction_id,created_at,event_type,user_id,delta,balance_after,reference,metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            txid,
            time.time(),
            event_type.strip(),
            str(user_id),
            delta,
            balance_after,
            str(reference) if reference is not None else None,
            metadata_json,
        ),
    )
    return txid


def read_entries(connection,
    *,
    user_id=None,
    event_type=None,
    reference=None,
    limit=100,
):
    """Read ledger rows newest-first from the supplied connection."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("ledger limit must be an integer between 1 and 1000")

    conditions = []
    parameters = []
    if user_id is not None:
        conditions.append("user_id = ?")
        parameters.append(str(user_id))
    if event_type is not None:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        conditions.append("event_type = ?")
        parameters.append(event_type.strip())
    if reference is not None:
        conditions.append("reference = ?")
        parameters.append(str(reference))

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    parameters.append(limit)
    rows = connection.execute(
        "SELECT entry_id,transaction_id,created_at,event_type,user_id,delta,"
        "balance_after,reference,metadata_json FROM economy_ledger"
        + where_clause
        + " ORDER BY entry_id DESC LIMIT ?",
        parameters,
    ).fetchall()

    entries = []
    for row in rows:
        entry = dict(row)
        raw_metadata = entry.pop("metadata_json")
        if raw_metadata is None:
            entry["metadata"] = None
        else:
            try:
                entry["metadata"] = json.loads(raw_metadata)
            except (TypeError, json.JSONDecodeError):
                entry["metadata"] = None
        entries.append(entry)
    return entries
