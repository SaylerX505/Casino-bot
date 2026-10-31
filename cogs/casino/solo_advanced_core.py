"""Shared game constants and fair outcome calculations for interactive solo games."""

import math
import secrets


MINES_TILE_COUNT = 20
MINES_MAX_COUNT = 8
MINES_RTP = 0.97
HILO_RTP = 0.97
HILO_MAX_MULTIPLIER = 1_000.0
_RECOVERY_DELAYS_SECONDS = (0.5, 1, 2, 4, 8, 15, 30)


def _mines_multiplier(mines_count: int, safe_reveals: int) -> float:
    """Calculate a 97%-RTP Mines multiplier from the exact survival odds."""
    if (
        isinstance(mines_count, bool)
        or not isinstance(mines_count, int)
        or not 1 <= mines_count <= MINES_MAX_COUNT
    ):
        raise ValueError("mine count must be between 1 and 8")
    safe_limit = MINES_TILE_COUNT - mines_count
    if (
        isinstance(safe_reveals, bool)
        or not isinstance(safe_reveals, int)
        or not 0 <= safe_reveals <= safe_limit
    ):
        raise ValueError("safe reveal count is outside the board")

    survival_probability = 1.0
    for index in range(safe_reveals):
        survival_probability *= (MINES_TILE_COUNT - mines_count - index) / (
            MINES_TILE_COUNT - index
        )
    if safe_reveals == 0:
        return 1.0
    return MINES_RTP / survival_probability

PLINKO_WEIGHTS = (1, 8, 28, 56, 70, 56, 28, 8, 1)
PLINKO_MULTIPLIERS = (3.0, 2.0, 1.5, 1.1, 0.0, 1.1, 1.5, 2.0, 3.0)


def _plinko_drop(_choice: str, stake: int) -> tuple[str, bool, int]:
    """Resolve one fair eight-step Plinko path and its matching landing bin."""
    if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
        raise ValueError("Plinko stake must be a positive integer")
    roll = secrets.randbits(8)
    path = "".join("R" if roll & (1 << bit) else "L" for bit in range(8))
    slot = path.count("R")
    multiplier = PLINKO_MULTIPLIERS[slot]
    result = f"slot={slot};path={path};multiplier={multiplier:g}x"
    if multiplier < 1.0:
        return result, False, 0
    payout = int(stake * multiplier)
    return result, True, max(stake, payout)


def _number_roulette(choice: str, stake: int) -> tuple[str, bool, int]:
    """European single-zero number roulette; exact-number match pays 36x gross."""
    if not choice.isascii() or not choice.isdigit() or not 0 <= int(choice) <= 36:
        raise ValueError("roulette choice must be a number from 0 to 36")
    result = str(secrets.randbelow(37))
    won = choice == result
    return result, won, stake * 36 if won else 0
