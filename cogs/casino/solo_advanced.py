"""Advanced solo commands with compatibility exports for game calculations and views."""

import asyncio
import logging

from discord.ext import commands

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.solo_advanced_art import _hilo_rank_label, _render_live_art
from cogs.casino.solo_advanced_base import InteractiveSoloWagerView
from cogs.casino.solo_advanced_core import (
    _RECOVERY_DELAYS_SECONDS,
    HILO_MAX_MULTIPLIER,
    HILO_RTP,
    MINES_MAX_COUNT,
    MINES_RTP,
    MINES_TILE_COUNT,
    PLINKO_MULTIPLIERS,
    PLINKO_WEIGHTS,
    _mines_multiplier,
    _number_roulette,
    _plinko_drop,
)
from cogs.casino.solo_advanced_views import HiLoView, MinesView
from cogs.casino.solo_betting import (
    COIN,
    MAX_BET,
    MIN_BET,
    claim_solo_session,
    has_active_solo_session,
    release_solo_session,
)
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


class AdvancedSoloGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _solo_cog(self):
        return self.bot.get_cog("SoloBetting")

    async def _command_ready(self, ctx):
        solo = self._solo_cog()
        if solo is None or not getattr(solo, "_wagers_ready", False):
            await ctx.send(
                view=StatusPanel(
                    "Game unavailable",
                    "Wagers are being checked. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
            return None
        return solo

    async def _start_interactive(self, ctx, bet: str, view_type, *view_args):
        solo = await self._command_ready(ctx)
        if solo is None:
            return
        amount = parse_bet_amount(bet, max_bet=MAX_BET)
        if not MIN_BET <= amount <= MAX_BET:
            usage = f"/{ctx.command.name} bet:100" if ctx.interaction is not None else f"{ctx.prefix}{ctx.command.name} 100"
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
                    "Finish or cash out your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            balance = await asyncio.to_thread(bank.get_balance, int(ctx.author.id))
        except Exception:
            logger.exception(
                "Could not verify interactive solo balance user_id=%s game_type=%s",
                ctx.author.id,
                getattr(view_type, "__name__", "unknown"),
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
        view = view_type(solo, ctx, amount, *view_args)
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel(
                    "Game active",
                    "Finish or cash out your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            return await view.start()
        except asyncio.CancelledError:
            raise
        except Exception:
            release_solo_session(ctx.author.id, view)
            logger.exception(
                "Could not start interactive solo game type=%s user_id=%s",
                view.game_type,
                ctx.author.id,
            )
            raise

    @commands.hybrid_command(
        name="mines",
        aliases=["الألغام", "الغام", "minesweeper", "mine"],
        description="Reveal safe tiles and cash out before a mine.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def mines(self, ctx, bet: str, mines_count: int = 3):
        if not 1 <= mines_count <= MINES_MAX_COUNT:
            return await ctx.send(
                view=StatusPanel(
                    "Invalid mine count",
                    "Choose 1–8 mines. More mines mean higher risk and bonuses.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        return await self._start_interactive(ctx, bet, MinesView, mines_count)

    @commands.hybrid_command(
        name="hilo",
        aliases=["hi-lo", "highlow", "hilow", "هاي_لو"],
        description="Guess higher or lower. Cash out anytime.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def hilo(self, ctx, bet: str):
        return await self._start_interactive(ctx, bet, HiLoView)

    @commands.hybrid_command(
        name="plinko",
        aliases=["بلينكو"],
        description="Drop a ball and see where it lands.",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def plinko(self, ctx, bet: str):
        solo = await self._command_ready(ctx)
        if solo is None:
            return
        return await solo._start_wager(
            ctx,
            bet,
            game_type="plinko",
            game_title="Plinko",
            choices={"drop": {"label": "Drop Ball", "emoji": "🔽"}},
            payout_numerator=100,
            odds_description="Center: 27.34% · Edge: 0.39%",
            instructions="Drop the ball.",
            command_name="plinko",
            payout_description="Bins: 3x · 2x · 1.5x · 1.1x · 0x · 1.1x · 1.5x · 2x · 3x.",
            outcome_resolver=_plinko_drop,
        )

    @commands.hybrid_command(
        name="numberroulette",
        aliases=["number-roulette", "nr", "روليت_الأرقام", "روليت_ارقام", "روليت_الارقام"],
        description="Bet on a number from 0 to 36 in European roulette",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def numberroulette(self, ctx, bet: str):
        solo = await self._command_ready(ctx)
        if solo is None:
            return
        choices = {str(number): {"label": str(number), "emoji": None} for number in range(37)}
        return await solo._start_wager(
            ctx,
            bet,
            game_type="number_roulette",
            game_title="NUMBER ROULETTE",
            choices=choices,
            payout_numerator=3600,
            odds_description="2.70%",
            instructions="Select a number, then confirm.",
            command_name="numberroulette",
            payout_description="Bonus: +35x.",
            outcome_resolver=_number_roulette,
        )


async def setup(bot):
    await bot.add_cog(AdvancedSoloGames(bot))



__all__ = [
    "COIN",
    "HILO_MAX_MULTIPLIER",
    "HILO_RTP",
    "MAX_BET",
    "MINES_MAX_COUNT",
    "MINES_RTP",
    "MINES_TILE_COUNT",
    "MIN_BET",
    "PLINKO_MULTIPLIERS",
    "PLINKO_WEIGHTS",
    "_RECOVERY_DELAYS_SECONDS",
    "AdvancedSoloGames",
    "HiLoView",
    "InteractiveSoloWagerView",
    "MinesView",
    "_hilo_rank_label",
    "_mines_multiplier",
    "_number_roulette",
    "_plinko_drop",
    "_render_live_art",
    "setup",
]
