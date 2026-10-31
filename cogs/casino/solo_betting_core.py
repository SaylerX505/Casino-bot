"""Shared solo wager constants, session ownership, and recovery operations."""

import asyncio
import logging
import secrets
from functools import partial

import discord

from cogs.casino import bank
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.utility.theme import StatusPanel, THEME

logger = logging.getLogger(__name__)


MIN_BET = 10
MAX_BET = 5_000
COIN = "🪙"
GROSS_WIN_MULTIPLIER_NUMERATOR = 190
GROSS_WIN_MULTIPLIER_DENOMINATOR = 100
SIDES = {
    "heads": {"label": "Heads", "emoji": "🪙"},
    "tails": {"label": "Tails", "emoji": "🪙"},
}
DICE_CHOICES = {str(number): {"label": str(number), "emoji": None} for number in range(1, 7)}
SLOT_SYMBOLS = ("🍒", "🍋", "🍊", "🍇", "⭐", "💎")
SLOT_TRIPLE_PAYOUTS = {"🍒": 500, "🍋": 600, "🍊": 700, "🍇": 800, "⭐": 1_000, "💎": 1_500}
SLOT_CHOICES = {"spin": {"label": "Spin", "emoji": "🎰"}}
_SOLO_GAME_ACCENTS = {
    "coinflip": THEME["gold"],
    "dice": THEME["teal"],
    "slots": THEME["red_dark"],
}
_RECOVERY_DELAYS_SECONDS = (2, 5, 15, 30, 60)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_SESSIONS: dict[int, discord.ui.View] = {}


def has_active_solo_session(user_id: int) -> bool:
    """Whether this member already has an open solo-game session."""
    current = _ACTIVE_SESSIONS.get(int(user_id))
    return current is not None and not getattr(current, "done", False) and not current.is_finished()


def claim_solo_session(user_id: int, view: discord.ui.View) -> bool:
    """Atomically reserve the member's one active solo-game session."""
    user_id = int(user_id)
    if has_active_solo_session(user_id):
        return False
    _ACTIVE_SESSIONS[user_id] = view
    return True


def release_solo_session(user_id: int, view: discord.ui.View) -> None:
    """Release a reserved session without clearing a newer session."""
    if _ACTIVE_SESSIONS.get(int(user_id)) is view:
        _ACTIVE_SESSIONS.pop(int(user_id), None)



def _gross_payout(
    stake: int,
    numerator: int = GROSS_WIN_MULTIPLIER_NUMERATOR,
    denominator: int = GROSS_WIN_MULTIPLIER_DENOMINATOR,
) -> int:
    """Return the advertised gross payout for a virtual-currency wager."""
    if stake <= 0 or numerator <= 0 or denominator <= 0:
        raise ValueError("stake and payout multiplier must be positive")
    return (stake * numerator) // denominator


def _spin_slots(_choice: str, stake: int) -> tuple[str, bool, int]:
    """Return a three-reel slot result, win flag, and gross payout in integer coins."""
    if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
        raise ValueError("slot stake must be a positive integer")
    symbols = tuple(secrets.choice(SLOT_SYMBOLS) for _ in range(3))
    result = " · ".join(symbols)
    if symbols[0] == symbols[1] == symbols[2]:
        return result, True, (stake * SLOT_TRIPLE_PAYOUTS[symbols[0]]) // 100
    if len(set(symbols)) == 2:
        return result, True, (stake * 170) // 100
    return result, False, 0


_STARTUP_PANEL_TASKS: set[asyncio.Task] = set()
_GAME_TITLES = {
    "coinflip": "COIN FLIP",
    "dice": "DICE",
    "slots": "SLOTS",
    "blackjack": "Blackjack",
    "mines": "Mines",
    "crash": "Crash",
    "plinko": "Plinko",
    "number_roulette": "NUMBER ROULETTE",
    "hilo": "Hi-Lo",
}


async def _publish_interrupted_wager_panel(bot, record, receipt):
    """Replace a stale game panel after the matching wager has been safely refunded."""
    if (
        receipt.get("status") != "refunded"
        or receipt.get("outcome") != "refund"
    ):
        return
    channel_id = record.get("channel_id")
    message_id = record.get("message_id")
    if not channel_id or not message_id:
        return

    try:
        await bot.wait_until_ready()
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        title = _GAME_TITLES.get(str(record.get("game_type")), "GAME")
        stake = int(record.get("stake", receipt.get("stake", 0)))
        coin_emoji = get_coin_emoji(getattr(channel, "guild", None))
        await message.edit(
            view=StatusPanel(
                "ROUND REFUNDED",
                (
                    f"Your **{title}** round was interrupted. "
                    f"Bet returned: **{stake:,} {coin_emoji}**. No result was recorded."
                ),
                "warning",
            )
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logger.info(
            "Interrupted wager refunded but original panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logger.warning(
            "Interrupted wager refunded but the bot cannot access its panel game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logger.warning(
            "Interrupted wager refunded but its old panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )
    except Exception:
        logger.exception(
            "Interrupted wager was refunded, but an unexpected error blocked panel cleanup game_id=%s",
            record.get("game_id"),
        )


async def recover_orphaned_casino_wagers(bot, schedule_recovery):
    """Refund unfinished solo bets at startup and close their stale Discord panels."""
    records = await asyncio.to_thread(bank.active_casino_wager_records)
    refunded = await asyncio.to_thread(bank.refund_orphaned_casino_wagers)
    active_ids = await asyncio.to_thread(bank.active_casino_wager_ids)
    active_set = set(active_ids)
    records_by_id = {str(record["game_id"]): record for record in records}

    for game_id, record in records_by_id.items():
        has_panel = bool(record.get("channel_id") and record.get("message_id"))
        if game_id in active_set:
            callback = (
                partial(_publish_interrupted_wager_panel, bot, record)
                if has_panel
                else None
            )
            schedule_recovery(game_id, force_refund=True, callback=callback)
            continue

        receipt = await asyncio.to_thread(bank.casino_wager_status, game_id)
        if (
            has_panel
            and receipt is not None
            and receipt.get("status") == "refunded"
            and receipt.get("outcome") == "refund"
        ):
            task = asyncio.create_task(
                _publish_interrupted_wager_panel(bot, record, receipt)
            )
            _STARTUP_PANEL_TASKS.add(task)
            task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

    for game_id in active_ids:
        if str(game_id) not in records_by_id:
            schedule_recovery(game_id, force_refund=True)

    return refunded, active_ids
