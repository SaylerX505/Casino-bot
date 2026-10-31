"""Solo betting commands with compatibility exports for existing game modules."""

import asyncio
import logging
from collections.abc import Callable

from discord.ext import commands

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.solo_betting_art import (
    _art_font,
    _draw_die,
    _draw_slot_symbol,
    _render_advanced_instant_art,
    _render_solo_game_art,
)
from cogs.casino.solo_betting_core import (
    _GAME_TITLES,
    _RECOVERY_DELAYS_SECONDS,
    _RECOVERY_TASKS,
    _SOLO_GAME_ACCENTS,
    COIN,
    DICE_CHOICES,
    GROSS_WIN_MULTIPLIER_DENOMINATOR,
    GROSS_WIN_MULTIPLIER_NUMERATOR,
    MAX_BET,
    MIN_BET,
    SIDES,
    SLOT_CHOICES,
    SLOT_SYMBOLS,
    SLOT_TRIPLE_PAYOUTS,
    _gross_payout,
    _publish_interrupted_wager_panel,
    _spin_slots,
    claim_solo_session,
    has_active_solo_session,
    recover_orphaned_casino_wagers,
    release_solo_session,
)
from cogs.casino.solo_betting_view import SoloWagerView
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


class SoloBetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._wagers_ready = False

    async def cog_load(self):
        try:
            refunded, active_ids = await recover_orphaned_casino_wagers(
                self.bot,
                self._schedule_recovery,
            )
        except Exception:
            self._wagers_ready = False
            logger.exception(
                "Solo casino wager recovery could not be verified; disabling new wagers"
            )
            return

        self._wagers_ready = True
        if refunded:
            logger.warning("Refunded %s interrupted solo casino wager(s) during startup", refunded)
        if active_ids:
            logger.warning("Scheduled recovery for %s unresolved solo casino wager(s)", len(active_ids))

    async def _settle_with_retries(self, game_id: str, *, force_refund: bool = False) -> dict:
        for attempt in range(3):
            try:
                return await asyncio.to_thread(
                    bank.settle_casino_wager,
                    game_id,
                    force_refund=force_refund,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Solo casino wager settlement failed game_id=%s attempt=%s/3",
                    game_id,
                    attempt + 1,
                )
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
        return {"settled": False, "reason": "database_error"}

    def _schedule_recovery(self, game_id: str, *, force_refund: bool, callback=None):
        game_id = str(game_id)
        existing = _RECOVERY_TASKS.get(game_id)
        if existing is not None and not existing.done():
            return existing

        async def worker():
            attempt = 0
            try:
                while True:
                    await asyncio.sleep(_RECOVERY_DELAYS_SECONDS[min(attempt, len(_RECOVERY_DELAYS_SECONDS) - 1)])
                    result = await self._settle_with_retries(game_id, force_refund=force_refund)
                    if result.get("settled") or result.get("reason") == "not_found":
                        if callback is not None:
                            try:
                                await callback(result)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Could not publish recovered coinflip result game_id=%s", game_id)
                        return
                    attempt += 1
                    if attempt % 3 == 0:
                        logger.error(
                            "Solo casino wager remains unresolved game_id=%s reason=%s attempts=%s",
                            game_id,
                            result.get("reason", "unknown"),
                            attempt,
                        )
            finally:
                current = asyncio.current_task()
                if _RECOVERY_TASKS.get(game_id) is current:
                    _RECOVERY_TASKS.pop(game_id, None)

        task = asyncio.create_task(worker())
        _RECOVERY_TASKS[game_id] = task
        return task

    async def _start_wager(
        self,
        ctx,
        bet: str,
        *,
        game_type: str,
        game_title: str,
        choices: dict,
        payout_numerator: int,
        payout_denominator: int = 100,
        odds_description: str,
        instructions: str,
        command_name: str,
        payout_description: str | None = None,
        outcome_resolver: Callable[[str, int], tuple[str, bool, int]] | None = None,
    ):
        if not self._wagers_ready:
            return await ctx.send(
                view=StatusPanel(
                    "Bets unavailable",
                    "Wagers are being checked. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        amount = parse_bet_amount(bet, max_bet=MAX_BET)
        if not MIN_BET <= amount <= MAX_BET:
            usage = f"/{command_name} bet:100" if ctx.interaction is not None else f"{ctx.prefix}{command_name} 100"
            return await ctx.send(
                view=StatusPanel(
                    "Invalid bet",
                    f"Use {usage}. Bet: {MIN_BET:,}–{MAX_BET:,} {get_coin_emoji(getattr(ctx, 'guild', None))}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        if has_active_solo_session(ctx.author.id):
            return await ctx.send(
                view=StatusPanel(
                    "Game active",
                    "Finish or cancel your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        try:
            balance = await asyncio.to_thread(bank.get_balance, int(ctx.author.id))
        except Exception:
            logger.exception(
                "Could not verify solo wager balance requester_id=%s game_type=%s",
                ctx.author.id,
                game_type,
            )
            return await ctx.send(
                view=StatusPanel(
                    "Balance unavailable",
                    "No bet was placed. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        if balance < amount:
            return await ctx.send(
                view=StatusPanel(
                    "Not enough coins",
                    f"Balance: **{balance:,} {get_coin_emoji(getattr(ctx, 'guild', None))}** · Bet: **{amount:,} {get_coin_emoji(getattr(ctx, 'guild', None))}**.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        view = SoloWagerView(
            self,
            ctx.author,
            amount,
            game_type=game_type,
            game_title=game_title,
            choices=choices,
            payout_numerator=payout_numerator,
            payout_denominator=payout_denominator,
            odds_description=odds_description,
            instructions=instructions,
            payout_description=payout_description,
            outcome_resolver=outcome_resolver,
            context=ctx,
        )
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel(
                    "Game active",
                    "Finish or cancel your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            if view._art_filename is not None:
                view.message = await ctx.send(view=view, file=view._art_file())
            else:
                view.message = await ctx.send(view=view)
        except asyncio.CancelledError:
            view._release_session()
            raise
        except Exception:
            view._release_session()
            logger.exception(
                "Could not create solo wager session type=%s requester_id=%s",
                game_type,
                ctx.author.id,
            )
            raise

    @commands.hybrid_command(
        name="coinflip",
        aliases=["flip", "cf", "coin", "coin-flip", "عملة", "رمي_العملة"],
        description="راهن بعملة افتراضية على نتيجة رمي العملة",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def coinflip(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="coinflip",
            game_title="COIN FLIP",
            choices=SIDES,
            payout_numerator=190,
            odds_description="50%",
            instructions="Choose heads or tails.",
            command_name="coinflip",
            payout_description="Bonus: +0.9x.",
        )

    @commands.hybrid_command(
        name="dice",
        aliases=["نرد", "زهر", "roll", "die"],
        description="راهن بعملة افتراضية على رقم النرد",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def dice(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="dice",
            game_title="DICE",
            choices=DICE_CHOICES,
            payout_numerator=570,
            odds_description="16.67%",
            instructions="Pick a number from 1 to 6.",
            payout_description="Bonus: +4.7x.",
            command_name="dice",
        )

    @commands.hybrid_command(
        name="slots",
        aliases=["slot", "سلوت", "ماكينة", "spin"],
        description="لف ماكينة الحظ مقابل رهان بعملة افتراضية",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def slots(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="slots",
            game_title="SLOTS",
            choices=SLOT_CHOICES,
            payout_numerator=170,
            odds_description="44.44% any win",
            instructions="Spin 3 reels.",
            command_name="slots",
            payout_description="Pair: +0.7x · Trips: +4x–14x bonus.",
            outcome_resolver=_spin_slots,
        )


async def setup(bot):
    await bot.add_cog(SoloBetting(bot))


__all__ = [
    "COIN",
    "DICE_CHOICES",
    "GROSS_WIN_MULTIPLIER_DENOMINATOR",
    "GROSS_WIN_MULTIPLIER_NUMERATOR",
    "MAX_BET",
    "MIN_BET",
    "SIDES",
    "SLOT_CHOICES",
    "SLOT_SYMBOLS",
    "SLOT_TRIPLE_PAYOUTS",
    "_GAME_TITLES",
    "_RECOVERY_DELAYS_SECONDS",
    "_SOLO_GAME_ACCENTS",
    "SoloBetting",
    "SoloWagerView",
    "_art_font",
    "_draw_die",
    "_draw_slot_symbol",
    "_gross_payout",
    "_publish_interrupted_wager_panel",
    "_render_advanced_instant_art",
    "_render_solo_game_art",
    "_spin_slots",
    "claim_solo_session",
    "has_active_solo_session",
    "recover_orphaned_casino_wagers",
    "release_solo_session",
    "setup",
]
