"""Blackjack slash and prefix command registration."""

import asyncio
import logging

from discord.ext import commands

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.blackjack_art import (
    BLACKJACK_ART_FILENAME,
    BLACKJACK_BRAND,
    _dealer_display_cards,
    render_blackjack_table,
)
from cogs.casino.blackjack_core import (
    DEALER_STANDS_AT,
    NATURAL_PAYOUT,
    PAYOUT_DENOMINATOR,
    RANKS,
    RECOVERY_DELAYS,
    REGULAR_PAYOUT,
    SUITS,
    _cards_text,
    _final_outcome,
    _hand_value,
    _initial_outcome,
    _is_natural_blackjack,
    _new_shuffled_deck,
)
from cogs.casino.blackjack_view import BlackjackView
from cogs.casino.solo_betting import (
    COIN,
    MAX_BET,
    MIN_BET,
    claim_solo_session,
    recover_orphaned_casino_wagers,
    release_solo_session,
)
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


class Blackjack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._wagers_ready = False
        self._recovery_tasks = {}

    async def cog_load(self):
        try:
            refunded, active_ids = await recover_orphaned_casino_wagers(
                self.bot,
                self._schedule_recovery,
            )
        except Exception:
            self._wagers_ready = False
            logger.exception("Blackjack wager recovery could not be verified; disabling new wagers")
            return
        self._wagers_ready = True
        if refunded:
            logger.warning("Refunded %s interrupted casino wager(s) during startup", refunded)
        if active_ids:
            logger.warning("Scheduled recovery for %s unresolved casino wager(s)", len(active_ids))

    async def _settle_with_retries(self, game_id, *, force_refund=False, **settlement):
        for attempt in range(3):
            try:
                return await asyncio.to_thread(
                    bank.settle_casino_wager, game_id,
                    force_refund=force_refund, **settlement,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Blackjack settlement failed game_id=%s attempt=%s", game_id, attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
        return {"settled": False, "reason": "database_error"}

    def _schedule_recovery(self, game_id, *, force_refund, settlement=None, callback=None):
        game_id = str(game_id)
        existing = self._recovery_tasks.get(game_id)
        if existing is not None and not existing.done():
            return existing
        final_state = dict(settlement or {})

        async def worker():
            attempt = 0
            try:
                while True:
                    await asyncio.sleep(RECOVERY_DELAYS[min(attempt, len(RECOVERY_DELAYS) - 1)])
                    result = await self._settle_with_retries(
                        game_id, force_refund=force_refund,
                        **({} if force_refund else final_state),
                    )
                    if result.get("settled") or result.get("reason") == "not_found":
                        if callback is not None:
                            try:
                                await callback(result)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Could not publish recovered blackjack result game_id=%s", game_id)
                        return
                    attempt += 1
            finally:
                if self._recovery_tasks.get(game_id) is asyncio.current_task():
                    self._recovery_tasks.pop(game_id, None)

        task = asyncio.create_task(worker())
        self._recovery_tasks[game_id] = task
        return task

    @commands.hybrid_command(
        name="blackjack",
        aliases=["bj", "black-jack", "بلجاك", "بلاكجاك"],
        description="Play Blackjack with virtual coins",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def blackjack(self, ctx, bet: str):
        if not self._wagers_ready:
            return await ctx.send(
                view=StatusPanel(
                    "Blackjack unavailable",
                    "Wagers are being checked. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        amount = parse_bet_amount(bet, max_bet=MAX_BET)
        if not MIN_BET <= amount <= MAX_BET:
            usage = "/blackjack bet:100" if ctx.interaction is not None else f"{ctx.prefix}blackjack 100"
            return await ctx.send(
                view=StatusPanel(
                    "Invalid bet",
                    f"Use {usage}. Bet: {MIN_BET:,}–{MAX_BET:,} {get_coin_emoji(getattr(ctx, 'guild', None))}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            balance = await asyncio.to_thread(bank.get_balance, int(ctx.author.id))
        except Exception:
            logger.exception(
                "Could not verify blackjack balance requester_id=%s",
                ctx.author.id,
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
        view = BlackjackView(self, ctx.author, amount, context=ctx)
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel("Game active", "Finish or cancel your current solo game first.", "warning"),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            view.message = await ctx.send(view=view, file=await view._art_file())
        except asyncio.CancelledError:
            release_solo_session(ctx.author.id, view)
            raise
        except Exception:
            release_solo_session(ctx.author.id, view)
            logger.exception("Could not create blackjack view requester_id=%s", ctx.author.id)
            raise


async def setup(bot):
    await bot.add_cog(Blackjack(bot))



__all__ = [
    "BLACKJACK_ART_FILENAME",
    "BLACKJACK_BRAND",
    "Blackjack",
    "BlackjackView",
    "DEALER_STANDS_AT",
    "NATURAL_PAYOUT",
    "PAYOUT_DENOMINATOR",
    "RANKS",
    "RECOVERY_DELAYS",
    "REGULAR_PAYOUT",
    "SUITS",
    "_cards_text",
    "_dealer_display_cards",
    "_final_outcome",
    "_hand_value",
    "_initial_outcome",
    "_is_natural_blackjack",
    "_new_shuffled_deck",
    "render_blackjack_table",
    "setup",
]
