"""Safe parsing for user-provided virtual-currency wager amounts."""

from __future__ import annotations


def parse_bet_amount(value: object, *, max_bet: int) -> int:
    """Parse a wager without converting an unbounded digit string to an integer.

    Invalid values become zero so each game can show its existing localized
    usage/range message. Inputs larger than the allowed maximum return
    max_bet + 1 without constructing a huge Python integer.
    """
    if isinstance(max_bet, bool) or not isinstance(max_bet, int) or max_bet < 0:
        raise ValueError("max_bet must be a non-negative integer")
    if not isinstance(value, str):
        return 0

    raw = value.strip().replace(",", "")
    if not raw or not raw.isascii() or not raw.isdigit():
        return 0

    significant = raw.lstrip("0") or "0"
    max_digits = str(max_bet)
    if len(significant) > len(max_digits):
        return max_bet + 1

    amount = int(significant)
    return amount if amount <= max_bet else max_bet + 1
