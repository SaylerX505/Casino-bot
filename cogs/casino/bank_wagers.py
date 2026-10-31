"""Atomic casino wager creation, double-down, settlement, and recovery."""

import time
import uuid

from cogs.casino import bank as _bank

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
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= _bank.SQLITE_MAX_INT:
        raise ValueError("casino wager stake must be a positive SQLite-safe integer")
    if not isinstance(won, bool):
        raise TypeError("casino wager won flag must be a boolean")
    if (
        isinstance(win_payout, bool)
        or not isinstance(win_payout, int)
        or not 0 <= win_payout <= _bank.SQLITE_MAX_INT
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
            or not 0 < field_value <= _bank.SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer when provided")

    with _bank._lock:
        c = _bank._connect()
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
            _bank._record_ledger(
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
                    _bank.now(),
                    channel_id,
                    message_id,
                ),
            )
            c.commit()
            return {"created": True, "balance_after": balance_after}
        except Exception:
            c.rollback()
            raise



def double_down_casino_wager(game_id, user_id, expected_stake):
    """Atomically double an active Blackjack stake after checking its expected value.

    The expected-stake precondition makes retries safe: if a previous call
    already doubled the wager, another call with the original amount cannot
    debit the account a second time.
    """
    game_id = str(game_id).strip()
    uid = str(user_id).strip()
    if not game_id or not uid:
        raise ValueError("game_id and user_id are required")
    if (
        isinstance(expected_stake, bool)
        or not isinstance(expected_stake, int)
        or not 0 < expected_stake <= _bank.SQLITE_MAX_INT
    ):
        raise ValueError("expected stake must be a positive SQLite-safe integer")

    with _bank._lock:
        c = _bank._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            wager = c.execute(
                "SELECT user_id,game_type,stake,status FROM casino_wagers WHERE game_id=?",
                (game_id,),
            ).fetchone()
            if wager is None:
                c.rollback()
                return {"doubled": False, "reason": "not_found"}
            if str(wager["user_id"]) != uid:
                c.rollback()
                return {"doubled": False, "reason": "not_owner"}
            if str(wager["game_type"]) != "blackjack":
                c.rollback()
                return {"doubled": False, "reason": "unsupported_game"}
            if str(wager["status"]) != "active":
                c.rollback()
                return {"doubled": False, "reason": "not_active"}

            current_stake = int(wager["stake"])
            if current_stake != expected_stake:
                c.rollback()
                return {
                    "doubled": False,
                    "reason": "stake_changed",
                    "stake": current_stake,
                }
            if current_stake > _bank.SQLITE_MAX_INT // 2:
                c.rollback()
                return {
                    "doubled": False,
                    "reason": "stake_limit",
                    "stake": current_stake,
                }

            wallet = c.execute(
                "SELECT balance FROM users WHERE user_id=?",
                (uid,),
            ).fetchone()
            if wallet is None:
                c.rollback()
                return {"doubled": False, "reason": "wallet_missing"}
            balance = int(wallet["balance"])
            if balance < current_stake:
                c.rollback()
                return {
                    "doubled": False,
                    "reason": "insufficient_funds",
                    "balance": balance,
                    "stake": current_stake,
                }

            new_stake = current_stake * 2
            balance_after = balance - current_stake
            c.execute(
                "UPDATE users SET balance=? WHERE user_id=?",
                (balance_after, uid),
            )
            updated = c.execute(
                "UPDATE casino_wagers SET stake=? "
                "WHERE game_id=? AND status='active' AND stake=?",
                (new_stake, game_id, current_stake),
            )
            if updated.rowcount != 1:
                c.rollback()
                return {"doubled": False, "reason": "stake_changed"}

            transaction_id = uuid.uuid4().hex
            _bank._record_ledger(
                c,
                event_type="casino_wager_stake",
                user_id=uid,
                delta=-current_stake,
                balance_after=balance_after,
                transaction_id=transaction_id,
                reference=game_id,
                metadata={
                    "game_type": "blackjack",
                    "stake": current_stake,
                    "total_stake": new_stake,
                    "reason": "double_down",
                },
            )
            c.commit()
            return {
                "doubled": True,
                "stake": new_stake,
                "additional_stake": current_stake,
                "balance_after": balance_after,
            }
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
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= _bank.SQLITE_MAX_INT:
        raise ValueError("casino wager stake must be a positive SQLite-safe integer")
    if not isinstance(won, bool):
        raise TypeError("casino wager won flag must be a boolean")
    if (
        isinstance(win_payout, bool)
        or not isinstance(win_payout, int)
        or not 0 <= win_payout <= _bank.SQLITE_MAX_INT
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
            or not 0 < field_value <= _bank.SQLITE_MAX_INT
        ):
            raise ValueError(f"{field_name} must be a positive integer when provided")

    with _bank._lock:
        c = _bank._connect()
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
            _bank._record_ledger(
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
            if won and balance_after_stake > _bank.SQLITE_MAX_INT - win_payout:
                # Avoid partial wallet settlement.
                balance_after = balance
                c.execute(
                    "UPDATE users SET balance=? WHERE user_id=?",
                    (balance_after, uid),
                )
                _bank._record_ledger(
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
                _bank._record_ledger(
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
                _bank._record_ledger(
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

            timestamp = _bank.now()
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
            or not 0 <= final_payout <= _bank.SQLITE_MAX_INT
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

    with _bank._lock:
        c = _bank._connect()
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
                if balance > _bank.SQLITE_MAX_INT - stake:
                    c.rollback()
                    return {"settled": False, "reason": "balance_limit"}
                balance_after = balance + stake
                c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                _bank._record_ledger(
                    c, event_type="casino_wager_refund", user_id=uid, delta=stake,
                    balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                    metadata={"game_type": str(wager["game_type"]), "stake": stake, "reason": "interrupted"},
                )
                status, outcome, payout = "refunded", "refund", 0
            elif selected_outcome == "push":
                if balance > _bank.SQLITE_MAX_INT - stake:
                    c.rollback()
                    return {"settled": False, "reason": "balance_limit"}
                balance_after = balance + stake
                c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                _bank._record_ledger(
                    c, event_type="casino_wager_push", user_id=uid, delta=stake,
                    balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                    metadata={"game_type": str(wager["game_type"]), "stake": stake, "gross_return": stake},
                )
                status, outcome, payout = "settled", "push", stake
            elif won:
                if balance <= _bank.SQLITE_MAX_INT - win_payout:
                    balance_after = balance + win_payout
                    c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                    _bank._record_ledger(
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
                    if balance > _bank.SQLITE_MAX_INT - stake:
                        c.rollback()
                        return {"settled": False, "reason": "balance_limit"}
                    balance_after = balance + stake
                    c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_after, uid))
                    _bank._record_ledger(
                        c, event_type="casino_wager_refund", user_id=uid, delta=stake,
                        balance_after=balance_after, transaction_id=transaction_id, reference=game_id,
                        metadata={"game_type": str(wager["game_type"]), "stake": stake, "reason": "payout_balance_limit"},
                    )
                    status, outcome, payout = "refunded", "balance_limit", 0
            else:
                balance_after = balance
                _bank._record_ledger(
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
                    _bank.now(),
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
    with _bank._lock:
        rows = _bank._connect().execute(
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
    with _bank._lock:
        rows = _bank._connect().execute(
            "SELECT game_id FROM casino_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [str(row["game_id"]) for row in rows]


def active_casino_wager_records():
    """Return durable panel references for wagers that may need startup recovery."""
    with _bank._lock:
        rows = _bank._connect().execute(
            "SELECT game_id,user_id,game_type,stake,channel_id,message_id "
            "FROM casino_wagers WHERE status='active' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def casino_wager_status(game_id):
    """Return the durable status and outcome needed to verify a recovery receipt."""
    with _bank._lock:
        row = _bank._connect().execute(
            "SELECT status,outcome,stake,game_type FROM casino_wagers WHERE game_id=?",
            (str(game_id),),
        ).fetchone()
        return dict(row) if row is not None else None
