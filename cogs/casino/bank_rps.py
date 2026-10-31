"""Persistent player-versus-player challenges and wager operations."""

import uuid

from cogs.casino import bank as _bank

def place_bet(player_a, player_b, amount):
    """Collect both stakes atomically; neither player is charged on failure."""
    a, b = str(player_a), str(player_b)
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount <= 0
        or amount > _bank.SQLITE_MAX_INT
        or a == b
    ):
        return False
    with _bank._lock:
        c = _bank._connect()
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
            _bank._record_ledger(
                c,
                event_type="pvp_bet_stake",
                user_id=a,
                delta=-amount,
                balance_after=int(row_a["balance"]) - amount,
                transaction_id=transaction_id,
                metadata={"counterparty": b, "stake": amount},
            )
            _bank._record_ledger(
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
        or amount > _bank.SQLITE_MAX_INT // 2
    ):
        return False
    if channel_id is not None and (
        isinstance(channel_id, bool)
        or not isinstance(channel_id, int)
        or not 0 < channel_id <= _bank.SQLITE_MAX_INT
    ):
        raise ValueError("channel_id must be a positive integer")

    timestamp = _bank.now()
    with _bank._lock:
        c = _bank._connect()
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
            or not 0 < field_value <= _bank.SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer")

    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE rps_challenges SET channel_id=?,message_id=?,updated_at=? "
                "WHERE game_id=? AND status IN ('pending','accepted')",
                (channel_id, message_id, _bank.now(), game_id),
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
    with _bank._lock:
        row = _bank._connect().execute(
            "SELECT game_id,player_a,player_b,amount,status,outcome,channel_id,message_id "
            "FROM rps_challenges WHERE game_id=?",
            (str(game_id),),
        ).fetchone()
        return dict(row) if row is not None else None


def accept_rps_challenge(game_id, user_id):
    """Atomically accept an invitation for its invited opponent and escrow both stakes."""
    game_id, uid = str(game_id).strip(), str(user_id).strip()
    if not game_id or not uid:
        return {"accepted": False, "reason": "not_found"}

    with _bank._lock:
        c = _bank._connect()
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
            if uid != str(challenge["player_b"]):
                c.rollback()
                return {"accepted": False, "reason": "not_authorized"}
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
                    (_bank.now(), game_id),
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
                    (_bank.now(), game_id),
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
            _bank._record_ledger(
                c,
                event_type="rps_wager_stake",
                user_id=a,
                delta=-amount,
                balance_after=balance_a - amount,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={"counterparty": b, "stake": amount},
            )
            _bank._record_ledger(
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
                    _bank.now(),
                    challenge["channel_id"],
                    challenge["message_id"],
                ),
            )
            c.execute(
                "UPDATE rps_challenges SET status='accepted',outcome=NULL,updated_at=? "
                "WHERE game_id=? AND status='pending'",
                (_bank.now(), game_id),
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
    with _bank._lock:
        c = _bank._connect()
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
                (outcome, outcome, _bank.now(), game_id),
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
    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute(
                "UPDATE rps_challenges SET status='expired',outcome='timeout',updated_at=? "
                "WHERE game_id=? AND status='pending'",
                (_bank.now(), game_id),
            ).rowcount
            c.commit()
            return changed == 1
        except Exception:
            c.rollback()
            raise


def expire_pending_rps_challenges():
    """Close every pre-acceptance invite at startup and return panels needing cleanup."""
    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT game_id,player_a,player_b,amount,channel_id,message_id "
                "FROM rps_challenges WHERE status='pending' ORDER BY created_at"
            ).fetchall()
            records = [dict(row) for row in rows]
            timestamp = _bank.now()
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
        or amount > _bank.SQLITE_MAX_INT
    ):
        return False

    with _bank._lock:
        c = _bank._connect()
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
            _bank._record_ledger(
                c,
                event_type="rps_wager_stake",
                user_id=a,
                delta=-amount,
                balance_after=int(row_a["balance"]) - amount,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={"counterparty": b, "stake": amount},
            )
            _bank._record_ledger(
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
                (game_id, a, b, amount, _bank.now()),
            )
            c.commit()
            return True
        except Exception:
            c.rollback()
            raise


def settle_rps_wager(game_id, winner_id=None):
    """Settle or refund a persisted RPS wager exactly once."""
    game_id = str(game_id)
    sqlite_max_int = _bank.SQLITE_MAX_INT
    with _bank._lock:
        c = _bank._connect()
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
                _bank._record_ledger(
                    c,
                    event_type="rps_wager_refund",
                    user_id=a,
                    delta=amount,
                    balance_after=balances[a] + amount,
                    transaction_id=settlement_transaction_id,
                    reference=game_id,
                    metadata={"stake": amount},
                )
                _bank._record_ledger(
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
                    _bank._record_ledger(
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
                (status, outcome, _bank.now(), game_id),
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
    with _bank._lock:
        rows = _bank._connect().execute(
            "SELECT game_id FROM rps_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        game_ids = [str(row["game_id"]) for row in rows]

    refunded = 0
    pending = 0
    for game_id in game_ids:
        try:
            result = _bank.settle_rps_wager(game_id)
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
    with _bank._lock:
        rows = _bank._connect().execute(
            "SELECT game_id FROM rps_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [str(row["game_id"]) for row in rows]




def set_rps_wager_message_reference(game_id, channel_id, message_id):
    """Attach the public challenge panel IDs to an active wager for restart cleanup."""
    game_id = str(game_id).strip()
    if not game_id:
        return False
    for field_name, field_value in (("channel_id", channel_id), ("message_id", message_id)):
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 < field_value <= _bank.SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer")

    with _bank._lock:
        c = _bank._connect()
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
    with _bank._lock:
        rows = _bank._connect().execute(
            "SELECT game_id,player_a,player_b,amount,channel_id,message_id "
            "FROM rps_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def rps_wager_status(game_id):
    """Read a durable RPS receipt for recovery decisions without changing balances."""
    with _bank._lock:
        row = _bank._connect().execute(
            "SELECT game_id,player_a,player_b,amount,status,outcome,channel_id,message_id "
            "FROM rps_wagers WHERE game_id=?",
            (str(game_id),),
        ).fetchone()
        return dict(row) if row is not None else None
