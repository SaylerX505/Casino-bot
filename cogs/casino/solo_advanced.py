"""Interactive solo casino games: Mines and Hi-Lo.

The stake is moved into the existing durable casino-wager ledger before a
player can act. Every terminal outcome is committed exactly once by
bank.settle_casino_wager(); an interrupted game is refunded by the existing
solo-betting startup recovery.
"""
# ruff: noqa: I001

import asyncio
import io
import logging
import secrets
import uuid

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.solo_betting import (
    MAX_BET,
    MIN_BET,
    _art_font,
    claim_solo_session,
    has_active_solo_session,
    release_solo_session,
)
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import THEME, StatusPanel

from cogs.casino.solo_advanced_art import _hilo_rank_label, _render_live_art
logger = logging.getLogger(__name__)

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


class InteractiveSoloWagerView(DiagnosticViewMixin, LayoutView):
    """Base for multistep wagers whose escrow and final outcome are database-backed."""

    def __init__(self, solo_cog, ctx, amount: int, *, game_type: str, game_title: str, accent: int):
        super().__init__(timeout=180)
        self.cog = solo_cog
        self.context = ctx
        self.requester = ctx.author
        self.coin_emoji = get_coin_emoji(getattr(ctx, 'guild', None))
        self.amount = int(amount)
        self.game_type = game_type
        self.game_title = game_title
        self.accent = accent
        self.game_id = uuid.uuid4().hex
        self.message = None
        self.done = False
        self.resolving = False
        self.started = False
        self.receipt = None
        self.start_error_message = None
        self._wager_created = False
        self._controls = []
        self._game_rows = []
        self.start_button = None
        self.cancel_button = None
        self._state_lock = asyncio.Lock()
        self._settlement_task = None
        self._replay_button = None
        self._pending_display_result = None
        self._pending_resolution = None
        self._panel_text = TextDisplay("")
        self._art_filename = f"{game_type}.png"
        self._art_bytes = b""

    def _build(self, rows):
        """Use the same V2 panel structure as the legacy solo games."""
        self._game_rows = rows
        self.start_button = discord.ui.Button(
            label="Start Round",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_start",
        )
        self.start_button.callback = self.begin_game
        self.cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_cancel_start",
        )
        self.cancel_button.callback = self.cancel_before_start
        self._set_controls_enabled(False)
        self._refresh()
        self._render_panel([ActionRow(self.start_button, self.cancel_button)])

    def _render_panel(self, rows):
        """Replace the panel body while keeping the legacy V2 container anatomy."""
        self.clear_items()
        self._panel_text = TextDisplay(self._content())
        self.add_item(
            Container(
                self._panel_text,
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{self._art_filename}",
                        description=f"{self.game_title} game artwork",
                    )
                ),
                Separator(),
                *rows,
                accent_color=self.accent,
            )
        )

    def _art_file(self):
        return discord.File(io.BytesIO(self._art_bytes), filename=self._art_filename)

    def _refresh(self):
        self._panel_text.content = self._content()
        self._art_bytes = self._render_art()

    def _render_art(self):
        raise NotImplementedError

    def _initial_content(self):
        return f"# {self.game_title}\n**Bet:** {self.amount:,} {self.coin_emoji}"

    def _content(self):
        if self.start_error_message:
            return f"# {self.game_title}\n{self.start_error_message}"
        if self.receipt is not None:
            return self._final_content()
        if self.resolving:
            return f"# {self.game_title}\nProcessing..."
        if not self.started:
            return self._initial_content()
        return f"# {self.game_title}\n**Bet:** {self.amount:,} {self.coin_emoji}"

    def _final_content(self):
        receipt = self.receipt or {}
        outcome = str(receipt.get("outcome") or "")
        balance_after = receipt.get("balance_after")
        balance_line = (
            f"\n**Balance:** {int(balance_after):,} {self.coin_emoji}"
            if balance_after is not None
            else ""
        )
        display = self._pending_display_result or str(receipt.get("result") or "")
        stake = int(receipt.get("stake", self.amount))
        bonus = max(0, int(receipt.get("payout", 0)) - stake)
        if receipt.get("status") == "refunded" or outcome == "balance_limit":
            return (
                f"# REFUNDED · {self.game_title}\n"
                f"{display or 'Bet returned.'}\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0{balance_line}"
            )
        if outcome == "push":
            return (
                f"# PUSH · {self.game_title}\n"
                f"{display or 'Bet returned.'}\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0{balance_line}"
            )
        if outcome == "win":
            return (
                f"# WIN · {self.game_title}\n"
                f"{display or 'Round won.'}\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: **+{bonus:,} {self.coin_emoji}**{balance_line}"
            )
        return (
            f"# LOSS · {self.game_title}\n"
            f"{display or 'Round lost.'}\n"
            f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0{balance_line}"
        )

    def _on_finished(self, receipt):
        """Update game-specific terminal controls before rendering the receipt."""

    def _set_controls_enabled(self, enabled: bool):
        for control in self._controls:
            control.disabled = not enabled

    def _set_prestart_controls_enabled(self, enabled: bool):
        for control in (self.start_button, self.cancel_button):
            if control is not None:
                control.disabled = not enabled

    def _prepare_refund(self, display_result: str):
        """Disable the panel and preserve a refund path even if artwork rendering fails."""
        self.resolving = True
        self._pending_display_result = display_result
        self._pending_resolution = {"force_refund": True}
        self._set_controls_enabled(False)
        self._set_prestart_controls_enabled(False)
        try:
            self._refresh()
        except Exception:
            logger.exception("Could not render refund state game_id=%s", self.game_id)
            self._panel_text.content = (
                f"# REFUNDING · {self.game_title}\n{display_result}"
            )

    def _schedule_settlement_worker(self):
        """Start at most one database settlement/retry task for this wager."""
        if self._settlement_task is None or self._settlement_task.done():
            resolution = self._pending_resolution or {"force_refund": True}
            self._settlement_task = asyncio.create_task(
                self._settlement_worker(**resolution)
            )

    async def _queue_refund(self, display_result: str):
        """Ensure an already-debited stake is refunded if startup or rendering fails."""
        self._prepare_refund(display_result)
        try:
            await self._edit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not publish refund state game_id=%s", self.game_id)
        finally:
            self._schedule_settlement_worker()

    async def _edit(self):
        if self.message is None:
            return False
        try:
            await self.message.edit(view=self, attachments=[self._art_file()])
            return True
        except asyncio.CancelledError:
            raise
        except discord.NotFound:
            logger.warning("Interactive wager panel was deleted game_id=%s", self.game_id)
        except discord.HTTPException:
            logger.warning("Could not edit interactive wager panel game_id=%s", self.game_id, exc_info=True)
        return False

    async def start_from_interaction(self, interaction):
        """Replace a finished game with a fresh pre-start panel."""
        self.message = interaction.message
        self._set_controls_enabled(False)
        self._set_prestart_controls_enabled(True)
        self._refresh()
        try:
            await interaction.response.edit_message(view=self, attachments=[self._art_file()])
            return True
        except Exception:
            logger.exception("Could not start replay game_id=%s", self.game_id)
            return False

    def _create_replay_view(self):
        raise NotImplementedError

    async def replay(self, interaction):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                view=StatusPanel("Not your game", "Only the original player can replay.", "warning"),
                ephemeral=True,
            )
        if not self.done or self.context is None:
            return await interaction.response.send_message(
                view=StatusPanel("Game closed", "Start a new game with the command.", "warning"),
                ephemeral=True,
            )
        try:
            balance = await asyncio.to_thread(bank.get_balance, int(self.requester.id))
        except Exception:
            logger.exception("Could not verify replay balance user_id=%s", self.requester.id)
            return await interaction.response.send_message(
                view=StatusPanel("Balance unavailable", "Try again shortly.", "warning"),
                ephemeral=True,
            )
        if balance < self.amount:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "Not enough coins",
                    f"Balance: {balance:,} {self.coin_emoji} · Bet: {self.amount:,} {self.coin_emoji}",
                    "warning",
                ),
                ephemeral=True,
            )
        replay_view = self._create_replay_view()
        if not claim_solo_session(self.requester.id, replay_view):
            replay_view.stop()
            return await interaction.response.send_message(
                view=StatusPanel("Game active", "Finish your current solo game first.", "warning"),
                ephemeral=True,
            )
        if not await replay_view.start_from_interaction(interaction):
            release_solo_session(self.requester.id, replay_view)

    async def start(self):
        """Show the game panel before taking any coins."""
        self._set_controls_enabled(False)
        self._set_prestart_controls_enabled(True)
        self._refresh()
        try:
            self.message = await self.context.send(
                view=self,
                file=self._art_file(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except asyncio.CancelledError:
            release_solo_session(self.requester.id, self)
            raise
        except Exception:
            release_solo_session(self.requester.id, self)
            logger.exception(
                "Could not publish interactive wager game_type=%s user_id=%s",
                self.game_type,
                self.requester.id,
            )
            raise
        return True

    async def begin_game(self, interaction):
        """Take the stake only after the player explicitly starts the round."""
        async with self._state_lock:
            if self.done or self.resolving or self.started:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        view=StatusPanel(
                            "Game closed",
                            "This round has already started or ended.",
                            "warning",
                        ),
                        ephemeral=True,
                    )
                return

            self.resolving = True
            self._set_prestart_controls_enabled(False)
            try:
                await interaction.response.defer()
            except asyncio.CancelledError:
                raise
            except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
                logger.warning(
                    "Could not acknowledge game start before escrow game_id=%s",
                    self.game_id,
                    exc_info=True,
                )
                self.done = True
                self.resolving = False
                self.start_error_message = "Could not start. No bet was placed."
                self._set_controls_enabled(False)
                self._set_prestart_controls_enabled(False)
                try:
                    self._refresh()
                    await self._edit()
                except Exception:
                    logger.exception("Could not close unstarted panel game_id=%s", self.game_id)
                finally:
                    release_solo_session(self.requester.id, self)
                    self.stop()
                return

            channel_id = getattr(getattr(self.context, "channel", None), "id", None)
            creation_task = asyncio.create_task(
                asyncio.to_thread(
                    bank.create_casino_wager,
                    self.game_id,
                    self.requester.id,
                    self.game_type,
                    self.amount,
                    "interactive",
                    "pending",
                    False,
                    0,
                    channel_id=int(channel_id) if channel_id else None,
                    message_id=int(self.message.id),
                )
            )
            try:
                receipt = await asyncio.shield(creation_task)
            except asyncio.CancelledError:
                self._prepare_refund(
                    "Start was interrupted. Checking and refunding the bet if needed."
                )
                self._settlement_task = asyncio.create_task(
                    self._settle_after_creation(creation_task)
                )
                try:
                    await self._edit()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Could not publish wager-start cancellation game_id=%s",
                        self.game_id,
                    )
                raise
            except Exception:
                logger.exception(
                    "Could not confirm interactive wager creation game_id=%s",
                    self.game_id,
                )
                await self._queue_refund(
                    "Could not confirm the bet. Checking and refunding if it was recorded."
                )
                return

            if not receipt.get("created"):
                balance = int(receipt.get("balance", 0))
                reason = receipt.get("reason")
                if reason == "insufficient_funds":
                    self.start_error_message = (
                        f"Balance **{balance:,} {self.coin_emoji}** is too low "
                        f"for a **{self.amount:,} {self.coin_emoji}** bet."
                    )
                elif reason == "duplicate_game":
                    self.start_error_message = (
                        "This round was already recorded. No new bet was taken."
                    )
                else:
                    self.start_error_message = (
                        "Bet declined. No coins were taken."
                    )
                self.done = True
                self.resolving = False
                self._set_controls_enabled(False)
                self._set_prestart_controls_enabled(False)
                try:
                    self._refresh()
                    await self._edit()
                except Exception:
                    logger.exception(
                        "Could not show rejected wager game_id=%s",
                        self.game_id,
                    )
                finally:
                    release_solo_session(self.requester.id, self)
                    self.stop()
                return

            self._wager_created = True
            self.started = True
            self.resolving = False
            try:
                self._set_controls_enabled(True)
                self._set_prestart_controls_enabled(False)
                self._refresh()
                self._render_panel(self._game_rows)
                if not await self._edit():
                    await self._queue_refund(
                        "Could not update the game. Returning the bet."
                    )
                    return
                await self._after_start()
            except asyncio.CancelledError:
                if not self.done and not self.resolving:
                    self._prepare_refund(
                        "Game stopped during startup. Returning the bet."
                    )
                    self._schedule_settlement_worker()
                raise
            except Exception:
                logger.exception(
                    "Interactive game startup failed after escrow game_id=%s game_type=%s",
                    self.game_id,
                    self.game_type,
                )
                if not self.done and not self.resolving:
                    await self._queue_refund(
                        "Game error during startup. Returning the bet safely."
                    )

    async def cancel_before_start(self, interaction):
        if self.done or self.resolving or self.started or self._wager_created:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    view=StatusPanel(
                        "Game closed",
                        "A started round cannot be cancelled.",
                        "warning",
                    ),
                    ephemeral=True,
                )
            return

        self.message = interaction.message or self.message
        self.done = True
        self.start_error_message = (
            "Cancelled before start. No bet was placed."
        )
        self._set_controls_enabled(False)
        self._set_prestart_controls_enabled(False)
        try:
            self._refresh()
            try:
                await interaction.response.edit_message(
                    view=self,
                    attachments=[self._art_file()],
                )
            except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
                await self._edit()
        except Exception:
            logger.exception(
                "Could not publish pre-start cancellation game_id=%s",
                self.game_id,
            )
        finally:
            release_solo_session(self.requester.id, self)
            self.stop()

    async def _after_start(self):
        return None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "Not your game",
                    "Only the original player can control this round.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        if self.done or self.resolving:
            await interaction.response.send_message(
                view=StatusPanel("Game closed", "The result is already being confirmed.", "warning"),
                ephemeral=True,
            )
            return False
        return True

    async def _begin_settlement(
        self,
        interaction=None,
        *,
        result: str = "ended",
        won: bool = False,
        payout: int = 0,
        outcome: str = "loss",
        display_result: str | None = None,
        force_refund: bool = False,
    ):
        if self.done or self.resolving:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.send_message(
                    view=StatusPanel("Game closed", "The result is being confirmed.", "warning"),
                    ephemeral=True,
                )
            return
        self.resolving = True
        self._pending_display_result = display_result or result
        self._pending_resolution = {
            "force_refund": force_refund,
            "result": result,
            "won": won,
            "payout": payout,
            "outcome": outcome,
        }
        self._set_controls_enabled(False)
        if interaction is not None:
            try:
                await interaction.response.defer()
            except (discord.HTTPException, discord.InteractionResponded):
                logger.debug(
                    "Could not defer terminal wager update game_id=%s",
                    self.game_id,
                    exc_info=True,
                )
        self._schedule_settlement_worker()

    async def _settle_after_creation(self, creation_task):
        """Wait for a cancelled database creation to finish before reconciling its stake."""
        try:
            try:
                await asyncio.shield(creation_task)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Interactive wager creation failed after cancellation game_id=%s",
                    self.game_id,
                )
            await self._settlement_worker(force_refund=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Could not reconcile cancelled wager creation game_id=%s",
                self.game_id,
            )

    async def _settlement_worker(
        self,
        *,
        force_refund: bool,
        result: str = "ended",
        won: bool = False,
        payout: int = 0,
        outcome: str = "loss",
    ):
        attempt = 0
        while not self.done:
            try:
                if force_refund:
                    receipt = await asyncio.to_thread(
                        bank.settle_casino_wager,
                        self.game_id,
                        force_refund=True,
                    )
                else:
                    receipt = await asyncio.to_thread(
                        bank.settle_casino_wager,
                        self.game_id,
                        final_result=result,
                        final_won=won,
                        final_payout=payout,
                        final_outcome=outcome,
                    )
                if receipt.get("settled"):
                    await self._finish(receipt)
                    return
                if receipt.get("reason") == "not_found":
                    await self._finish_unrecorded()
                    return
                logger.error(
                    "Interactive wager settlement unresolved game_id=%s reason=%s attempt=%s",
                    self.game_id,
                    receipt.get("reason", "unknown"),
                    attempt + 1,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Interactive wager settlement failed game_id=%s attempt=%s",
                    self.game_id,
                    attempt + 1,
                )
            delay = _RECOVERY_DELAYS_SECONDS[min(attempt, len(_RECOVERY_DELAYS_SECONDS) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    async def _finish(self, receipt):
        self.receipt = receipt
        self.done = True
        self.resolving = False
        try:
            self._set_controls_enabled(False)
            self._set_prestart_controls_enabled(False)
            try:
                self._on_finished(receipt)
                self._refresh()
            except Exception:
                logger.exception("Could not render settled wager panel game_id=%s", self.game_id)
                self._panel_text.content = self._final_content()
            self._replay_button = discord.ui.Button(
                label="Play Again",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{self.game_type}_replay",
            )
            self._replay_button.callback = self.replay
            self._render_panel([ActionRow(self._replay_button)])
            try:
                updated = await self._edit()
                if not updated:
                    logger.error(
                        "Wager was settled but final panel could not be edited game_id=%s status=%s",
                        self.game_id,
                        receipt.get("status"),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not publish settled wager receipt game_id=%s", self.game_id)
        finally:
            release_solo_session(self.requester.id, self)

    async def _finish_unrecorded(self):
        self.done = True
        self.resolving = False
        self.start_error_message = (
            "Bet record not found. No result was announced. Check your balance "
            "before trying again. Contact staff if the issue continues."
        )
        self._set_controls_enabled(False)
        self._set_prestart_controls_enabled(False)
        try:
            self._refresh()
        except Exception:
            logger.exception(
                "Could not render unrecorded wager closure game_id=%s",
                self.game_id,
            )
            self._panel_text.content = (
                f"# {self.game_title}\n{self.start_error_message}"
            )
        try:
            await self._edit()
        finally:
            release_solo_session(self.requester.id, self)
            self.stop()

    async def _on_timeout(self):
        await self._begin_settlement(force_refund=True, display_result="Time expired. Bet returned safely.")

    async def on_timeout(self):
        if self.done:
            if self._replay_button is not None:
                self._replay_button.disabled = True
                await self._edit()
            self.stop()
            return
        if self.resolving:
            return
        if not self._wager_created:
            self.done = True
            self.start_error_message = (
                "Start timed out. No bet was placed."
            )
            self._set_controls_enabled(False)
            self._set_prestart_controls_enabled(False)
            try:
                self._refresh()
                await self._edit()
            except Exception:
                logger.exception(
                    "Could not close unstarted game on timeout game_id=%s",
                    self.game_id,
                )
            finally:
                release_solo_session(self.requester.id, self)
                self.stop()
            return
        await self._on_timeout()


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
        return _render_live_art(
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
