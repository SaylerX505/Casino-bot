"""Mines and Hi-Lo controls layered on the shared interactive wager view."""

import asyncio
import logging
import secrets

import discord
from discord.ui import ActionRow

from cogs.casino import bank, solo_advanced as _advanced
from cogs.casino.solo_advanced_art import _hilo_rank_label
from cogs.casino.solo_advanced_base import InteractiveSoloWagerView
from cogs.casino.solo_advanced_core import (
    HILO_MAX_MULTIPLIER,
    HILO_RTP,
    MINES_MAX_COUNT,
    MINES_RTP,
    MINES_TILE_COUNT,
    _mines_multiplier,
)
from cogs.casino.solo_betting import COIN
from cogs.utility.theme import THEME, StatusPanel

logger = logging.getLogger(__name__)


class MinesView(InteractiveSoloWagerView):
    def _create_replay_view(self):
        return MinesView(self.cog, self.context, self.amount, self.mines_count)

    def __init__(self, solo_cog, ctx, amount: int, mines_count: int):
        self.mines_count = mines_count
        self.mines = set(secrets.SystemRandom().sample(range(MINES_TILE_COUNT), mines_count))
        self.revealed: set[int] = set()
        self._pending_mine_hit = None
        super().__init__(
            solo_cog,
            ctx,
            amount,
            game_type="mines",
            game_title="MINES",
            accent=THEME["green"],
        )
        self.tile_buttons = {}
        for index in range(MINES_TILE_COUNT):
            button = discord.ui.Button(
                label=f"{index + 1:02d}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"mines_tile_{index + 1}",
                disabled=True,
            )
            button.callback = self._tile_callback(index)
            self.tile_buttons[index] = button
        self.cashout_button = discord.ui.Button(
            label="Cash Out",
            style=discord.ButtonStyle.secondary,
            custom_id="mines_cashout",
            disabled=True,
        )
        self.cashout_button.callback = self.cashout
        self._controls = list(self.tile_buttons.values()) + [self.cashout_button]
        rows = [
            ActionRow(*(self.tile_buttons[index] for index in range(row * 5, row * 5 + 5)))
            for row in range(4)
        ]
        rows.append(ActionRow(self.cashout_button))
        self._build(rows)

    def _tile_callback(self, index):
        async def callback(interaction):
            await self.reveal(interaction, index)
        return callback

    def _initial_content(self):
        return f"# MINES\n**Bet:** {self.amount:,} {self.coin_emoji} · **Mines:** {self.mines_count}"

    def _render_art(self):
        show_mines = (
            self.receipt is not None
            and self.receipt.get("outcome") in {"loss", "win", "push"}
            and self.receipt.get("status") != "refunded"
        )
        actual_result = show_mines
        return _advanced._render_live_art(
            "mines",
            revealed=self.revealed,
            mines=self.mines,
            reveal_mines=show_mines,
            mines_count=self.mines_count,
            multiplier=_mines_multiplier(self.mines_count, len(self.revealed)),
            final=show_mines,
            won=self.receipt.get("outcome") == "win" if actual_result else None,
        )

    def _content(self):
        if self.start_error_message or self.receipt is not None or self.resolving or not self.started:
            return super()._content()
        multiplier = _mines_multiplier(self.mines_count, len(self.revealed)) if self.revealed else 1.0
        cashout = int(self.amount * multiplier) if self.revealed else self.amount
        safe_chance = (MINES_TILE_COUNT - self.mines_count - len(self.revealed)) / (
            MINES_TILE_COUNT - len(self.revealed)
        )
        bonus = max(0, cashout - self.amount)
        return (
            f"# MINES\n"
            f"**Bet:** {self.amount:,} {self.coin_emoji} · **Safe:** {len(self.revealed)}\n"
            f"**Next safe:** {safe_chance:.0%} · **Bonus:** +{bonus:,} {self.coin_emoji} · **{multiplier:.2f}x**"
        )

    def _on_finished(self, receipt):
        for button in self.tile_buttons.values():
            button.disabled = True

    def _set_controls_enabled(self, enabled: bool):
        for index, button in self.tile_buttons.items():
            button.disabled = not enabled or index in self.revealed
        self.cashout_button.disabled = not enabled

    def _disable_tile(self, index, *, mine=False):
        button = self.tile_buttons[index]
        button.disabled = True
        if mine:
            button.style = discord.ButtonStyle.secondary
        else:
            button.style = discord.ButtonStyle.secondary

    async def reveal(self, interaction, index: int):
        async with self._state_lock:
            if self.done or self.resolving or index in self.revealed:
                return
            if index in self.mines:
                self._pending_mine_hit = index
                await self._begin_settlement(
                    interaction,
                    result=f"mine:{index + 1}",
                    display_result=f"Mine hit: tile **{index + 1:02d}**.",
                    won=False,
                    payout=0,
                    outcome="loss",
                )
                return
            self.revealed.add(index)
            self._disable_tile(index)
            self.cashout_button.label = "Cash Out"
            try:
                self._refresh()
            except Exception:
                logger.exception(
                    "Could not render safe Mines reveal game_id=%s tile=%s",
                    self.game_id,
                    index + 1,
                )
                await self._begin_settlement(
                    interaction,
                    force_refund=True,
                    display_result="Could not update the board. Bet returned.",
                )
                return
            try:
                await interaction.response.edit_message(view=self)
            except asyncio.CancelledError:
                raise
            except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
                logger.warning("Could not publish safe Mines reveal game_id=%s tile=%s", self.game_id, index + 1, exc_info=True)
                await self._edit()

    async def cashout(self, interaction):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            if not self.revealed:
                await self._begin_settlement(
                    interaction,
                    force_refund=True,
                    display_result="Cashed out before the first reveal. Bet returned.",
                )
                return
            multiplier = _mines_multiplier(self.mines_count, len(self.revealed))
            payout = max(self.amount, int(self.amount * multiplier))
            outcome = "push" if payout <= self.amount else "win"
            await self._begin_settlement(
                interaction,
                result=f"cashout:{len(self.revealed)};multiplier={multiplier:.2f}x",
                display_result=(
                    f"Safe tiles: **{len(self.revealed)}** · Multiplier: **{multiplier:.2f}x**."
                ),
                won=outcome == "win",
                payout=payout if outcome == "win" else 0,
                outcome=outcome,
            )

    async def _on_timeout(self):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            if self.revealed:
                multiplier = _mines_multiplier(self.mines_count, len(self.revealed))
                payout = max(self.amount, int(self.amount * multiplier))
                outcome = "push" if payout <= self.amount else "win"
                await self._begin_settlement(
                    result=f"timeout_cashout:{len(self.revealed)};multiplier={multiplier:.2f}x",
                    display_result=f"Time expired · Auto cash out at **{multiplier:.2f}x**.",
                    won=outcome == "win",
                    payout=payout if outcome == "win" else 0,
                    outcome=outcome,
                )
            else:
                await self._begin_settlement(
                    force_refund=True,
                    display_result="Time expired before the first reveal. Bet returned.",
                )


class HiLoView(InteractiveSoloWagerView):
    def _create_replay_view(self):
        return HiLoView(self.cog, self.context, self.amount)

    def __init__(self, solo_cog, ctx, amount: int):
        self.current_rank = secrets.randbelow(13) + 2
        self.current_suit = secrets.choice(("♠", "♥", "♦", "♣"))
        self.previous_suit = "♠"
        self.previous_rank = None
        self.streak = 0
        self.multiplier = 1.0
        self.last_guess = None
        super().__init__(
            solo_cog,
            ctx,
            amount,
            game_type="hilo",
            game_title="HI-LO",
            accent=THEME["teal"],
        )
        self.higher_button = discord.ui.Button(
            label="Higher",
            style=discord.ButtonStyle.secondary,
            custom_id="hilo_higher",
            disabled=True,
        )
        self.higher_button.callback = self.guess_higher
        self.lower_button = discord.ui.Button(
            label="Lower",
            style=discord.ButtonStyle.secondary,
            custom_id="hilo_lower",
            disabled=True,
        )
        self.lower_button.callback = self.guess_lower
        self.cashout_button = discord.ui.Button(
            label="Cash Out",
            style=discord.ButtonStyle.secondary,
            custom_id="hilo_cashout",
            disabled=True,
        )
        self.cashout_button.callback = self.cashout
        self._controls = [self.higher_button, self.lower_button, self.cashout_button]
        self._build([ActionRow(*self._controls)])

    def _initial_content(self):
        return f"# HI-LO\n**Bet:** {self.amount:,} {self.coin_emoji} · **Tie loses**"

    def _render_art(self):
        outcome = (
            self.receipt.get("outcome")
            if self.receipt is not None and self.receipt.get("status") != "refunded"
            else None
        )
        return _render_live_art(
            "hilo",
            current_rank=self.current_rank,
            previous_rank=self.previous_rank if self.receipt is not None else None,
            current_suit=self.current_suit,
            previous_suit=self.previous_suit,
            multiplier=self.multiplier,
            final=self.receipt is not None,
            won=True if outcome == "win" else False if outcome == "loss" else None,
        )

    def _content(self):
        if self.start_error_message or self.receipt is not None or self.resolving or not self.started:
            return super()._content()
        higher = (14 - self.current_rank) / 13
        lower = (self.current_rank - 2) / 13
        bonus = max(0, int(self.amount * self.multiplier) - self.amount)
        return (
            f"# HI-LO · **{_hilo_rank_label(self.current_rank)}{self.current_suit}**\n"
            f"**Bet:** {self.amount:,} {self.coin_emoji} · **Streak:** {self.streak}\n"
            f"**H:** {higher:.0%} · **L:** {lower:.0%} · **Tie:** 7.7% · **Bonus:** +{bonus:,} {self.coin_emoji} · **{self.multiplier:.2f}x**"
        )

    def _set_controls_enabled(self, enabled: bool):
        self.higher_button.disabled = not enabled or self.current_rank == 14
        self.lower_button.disabled = not enabled or self.current_rank == 2
        self.cashout_button.disabled = not enabled

    async def _guess(self, interaction, direction: str):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            previous = self.current_rank
            next_rank = secrets.randbelow(13) + 2
            if (direction == "higher" and previous == 14) or (direction == "lower" and previous == 2):
                self._set_controls_enabled(True)
                try:
                    await interaction.response.edit_message(view=self)
                except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
                    logger.warning("Could not refresh boundary Hi-Lo controls game_id=%s", self.game_id, exc_info=True)
                return
            correct = next_rank > previous if direction == "higher" else next_rank < previous
            self.previous_rank = previous
            self.previous_suit = self.current_suit
            self.current_rank = next_rank
            self.current_suit = secrets.choice(("♠", "♥", "♦", "♣"))
            self.last_guess = direction
            if not correct:
                await self._begin_settlement(
                    interaction,
                    result=f"wrong:{direction}:{previous}:{next_rank}",
                    display_result=(
                        f"Guess: **{'Higher' if direction == 'higher' else 'Lower'}** · "
                        f"Next: **{_hilo_rank_label(next_rank)}{self.current_suit}**."
                    ),
                    won=False,
                    payout=0,
                    outcome="loss",
                )
                return

            probability = (14 - previous) / 13 if direction == "higher" else (previous - 2) / 13
            self.streak += 1
            self.multiplier = min(HILO_MAX_MULTIPLIER, self.multiplier * HILO_RTP / probability)
            if self.multiplier >= HILO_MAX_MULTIPLIER:
                await self._begin_settlement(
                    interaction,
                    result=f"cap:{self.streak};multiplier={self.multiplier:.2f}x",
                    display_result=f"Multiplier cap reached after **{self.streak}** wins · **{self.multiplier:.2f}x**.",
                    won=True,
                    payout=max(self.amount + 1, int(self.amount * self.multiplier)),
                    outcome="win",
                )
                return

            self._set_controls_enabled(True)
            try:
                self._refresh()
            except Exception:
                logger.exception(
                    "Could not render correct Hi-Lo guess game_id=%s",
                    self.game_id,
                )
                await self._begin_settlement(
                    interaction,
                    force_refund=True,
                    display_result=(
                        "Could not update the card. Bet returned."
                    ),
                )
                return
            try:
                await interaction.response.edit_message(view=self)
            except asyncio.CancelledError:
                raise
            except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
                logger.warning("Could not publish correct Hi-Lo guess game_id=%s", self.game_id, exc_info=True)
                await self._edit()

    async def guess_higher(self, interaction):
        await self._guess(interaction, "higher")

    async def guess_lower(self, interaction):
        await self._guess(interaction, "lower")

    async def cashout(self, interaction):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            payout = max(self.amount, int(self.amount * self.multiplier))
            outcome = "push" if payout <= self.amount else "win"
            await self._begin_settlement(
                interaction,
                result=f"cashout:{self.streak};multiplier={self.multiplier:.2f}x",
                display_result=(
                    "Cashed out before the first guess. Bet returned."
                    if self.streak == 0
                    else f"Cashed out after **{self.streak}** correct guesses at **{self.multiplier:.2f}x**."
                ),
                won=outcome == "win",
                payout=payout if outcome == "win" else 0,
                outcome=outcome,
            )

    async def _on_timeout(self):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            payout = max(self.amount, int(self.amount * self.multiplier))
            outcome = "push" if self.streak == 0 or payout <= self.amount else "win"
            await self._begin_settlement(
                result=f"timeout_cashout:{self.streak};multiplier={self.multiplier:.2f}x",
                display_result=(
                    "Time expired before the first guess. Bet returned."
                    if self.streak == 0
                    else f"Time expired · Auto cash out at **{self.multiplier:.2f}x**."
                ),
                won=outcome == "win",
                payout=payout if outcome == "win" else 0,
                outcome=outcome,
            )
