"""Shared escrow, interaction, replay, and recovery lifecycle for solo games."""

import asyncio
import io
import logging
import secrets
import uuid

import discord
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.solo_advanced_art import _render_live_art
from cogs.casino.solo_advanced_core import _RECOVERY_DELAYS_SECONDS
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

logger = logging.getLogger(__name__)


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
