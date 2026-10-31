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
    if not raw or not raw.isascii():
        return 0

    multiplier = 1
    suffix = raw[-1].lower()
    if suffix in {"k", "m", "b"}:
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]
        raw = raw[:-1]

    if not raw:
        return 0
    parts = raw.split(".")
    if len(parts) > 2 or any(not part or not part.isascii() or not part.isdigit() for part in parts):
        return 0
    if len(parts) == 2 and suffix not in {"k", "m", "b"}:
        return 0

    whole = parts[0].lstrip("0") or "0"
    max_digits = str(max_bet)
    if len(whole) > len(max_digits):
        return max_bet + 1

    amount = int(whole) * multiplier
    if len(parts) == 2:
        fraction = parts[1].rstrip("0")
        if fraction:
            precision = len(str(multiplier)) - 1
            if len(fraction) > precision:
                return 0
            divisor = 10 ** len(fraction)
            scaled_fraction = int(fraction) * multiplier
            if scaled_fraction % divisor:
                return 0
            amount += scaled_fraction // divisor

    return amount if amount <= max_bet else max_bet + 1
