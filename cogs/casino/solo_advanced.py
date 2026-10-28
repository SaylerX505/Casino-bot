"""Interactive solo casino games: Mines, Crash, and Hi-Lo.

The stake is moved into the existing durable casino-wager ledger before a
player can act. Every terminal outcome is committed exactly once by
bank.settle_casino_wager(); an interrupted game is refunded by the existing
solo-betting startup recovery.
"""
# ruff: noqa: I001

import asyncio
import io
import logging
import math
import secrets
import time
import uuid

import discord
from PIL import Image, ImageDraw
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.solo_betting import (
    COIN,
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

MINES_TILE_COUNT = 20
MINES_MAX_COUNT = 8
MINES_RTP = 0.97
CRASH_RTP = 0.97
CRASH_GROWTH_PER_SECOND = 1.16
CRASH_MAX_MULTIPLIER = 100.0
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


def _crash_point() -> float:
    """Generate a two-decimal Crash point with a 97% theoretical return.

    Values are capped at 100x so a live session always reaches a terminal
    state quickly; the cap is also shown in the game's rules.
    """
    roll = secrets.randbelow(1_000_000)
    value = math.floor(97_000_000 / (1_000_000 - roll)) / 100
    return min(CRASH_MAX_MULTIPLIER, max(1.0, value))


def _hilo_rank_label(rank: int) -> str:
    return {11: "J", 12: "Q", 13: "K", 14: "A"}.get(rank, str(rank))


def _render_live_art(
    game_type: str,
    *,
    revealed: set[int] | None = None,
    mines: set[int] | None = None,
    reveal_mines: bool = False,
    mines_count: int = 3,
    multiplier: float = 1.0,
    elapsed: float = 0.0,
    crash_point: float | None = None,
    current_rank: int | None = None,
    previous_rank: int | None = None,
    final: bool = False,
    won: bool | None = None,
) -> bytes:
    """Create the game-specific dark, result-aware game banner."""
    width, height = 1200, 420
    palette = {
        "mines": ((18, 37, 34), (8, 15, 20), (87, 210, 166), "MINES"),
        "crash": ((48, 25, 32), (10, 12, 22), (244, 137, 112), "CRASH"),
        "hilo": ((27, 29, 51), (8, 12, 22), (191, 174, 255), "HI-LO"),
    }
    top, bottom, accent, title = palette[game_type]
    image = Image.new("RGB", (width, height), bottom)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    for offset in range(-height, width, 100):
        draw.line((offset, 0, offset + height, height), fill=(30, 39, 49), width=1)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=30, outline=accent, width=3)
    draw.text((58, 43), "ÇUKUR  /  CASINO", font=_art_font(20, bold=True), fill=accent)
    draw.text((58, 77), title, font=_art_font(43, bold=True), fill=(247, 245, 239))
    draw.line((58, 140, 326, 140), fill=accent, width=4)

    if game_type == "mines":
        revealed = revealed or set()
        mines = mines or set()
        draw.text((64, 181), f"{mines_count} MINES", font=_art_font(23, bold=True), fill=(240, 242, 234))
        draw.text((64, 220), f"{len(revealed)} SAFE", font=_art_font(20, bold=True), fill=accent)
        draw.text((64, 258), "20 TILES", font=_art_font(18, bold=True), fill=(181, 195, 196))
        x0, y0, tile_w, tile_h, gap_x, gap_y = 390, 58, 78, 65, 12, 12
        for index in range(MINES_TILE_COUNT):
            row, col = divmod(index, 5)
            left = x0 + col * (tile_w + gap_x)
            top_y = y0 + row * (tile_h + gap_y)
            is_mine = reveal_mines and index in mines
            is_safe = index in revealed
            if is_mine:
                fill, outline, symbol = (96, 34, 45), (245, 110, 110), "×"
            elif is_safe:
                fill, outline, symbol = (26, 91, 72), accent, "◆"
            else:
                fill, outline, symbol = (24, 34, 45), (65, 81, 94), f"{index + 1:02d}"
            draw.rounded_rectangle(
                (left, top_y, left + tile_w, top_y + tile_h),
                radius=11,
                fill=fill,
                outline=outline,
                width=3 if is_mine or is_safe else 2,
            )
            draw.text(
                (left + tile_w // 2, top_y + tile_h // 2),
                symbol,
                font=_art_font(23 if is_mine or is_safe else 17, bold=True),
                fill=(255, 245, 231) if is_mine or is_safe else (155, 172, 183),
                anchor="mm",
            )
        draw.text((645, 373), "REVEAL · MULTIPLY · CASH OUT", font=_art_font(14, bold=True), fill=accent, anchor="mm")
    elif game_type == "crash":
        left, top_y, right, bottom_y = 390, 70, 880, 342
        for i in range(1, 6):
            y = top_y + (bottom_y - top_y) * i // 6
            draw.line((left, y, right, y), fill=(38, 49, 60), width=1)
        for i in range(1, 8):
            x = left + (right - left) * i // 8
            draw.line((x, top_y, x, bottom_y), fill=(38, 49, 60), width=1)
        x_progress = min(1.0, max(0.06, elapsed / 32))
        y_progress = min(1.0, max(0.04, math.log(max(1.0, multiplier), CRASH_GROWTH_PER_SECOND) / 32))
        line_points = [(left, bottom_y)]
        for step in range(1, 31):
            x_ratio = step / 30
            x = left + int((right - left) * x_ratio * x_progress)
            y = bottom_y - int((bottom_y - top_y) * min(0.94, (x_ratio ** 1.7) * y_progress))
            line_points.append((x, y))
        line_points.append((left + int((right - left) * x_progress), bottom_y - int((bottom_y - top_y) * y_progress)))
        draw.line(
            line_points,
            fill=accent if not final or won is not False else (247, 100, 103),
            width=5,
            joint="curve",
        )
        dot_x = left + int((right - left) * x_progress)
        dot_y = bottom_y - int((bottom_y - top_y) * y_progress)
        draw.ellipse((dot_x - 8, dot_y - 8, dot_x + 8, dot_y + 8), fill=(255, 244, 221), outline=accent, width=3)
        draw.rounded_rectangle((924, 83, 1141, 321), radius=24, fill=(19, 24, 34), outline=accent, width=3)
        draw.text((1032, 127), "MULTIPLIER", font=_art_font(15, bold=True), fill=(177, 190, 195), anchor="mm")
        draw.text((1032, 178), f"{multiplier:.2f}x", font=_art_font(37, bold=True), fill=(248, 245, 237), anchor="mm")
        draw.line((951, 211, 1114, 211), fill=(68, 78, 86), width=2)
        if final and crash_point is not None:
            draw.text((1032, 251), f"CRASH {crash_point:.2f}x", font=_art_font(16, bold=True), fill=(249, 132, 117), anchor="mm")
        elif final and won is True:
            draw.text((1032, 251), "CASHED OUT", font=_art_font(15, bold=True), fill=accent, anchor="mm")
        elif final:
            draw.text((1032, 251), "ROUND CLOSED", font=_art_font(13, bold=True), fill=accent, anchor="mm")
        else:
            draw.text((1032, 251), "CASH OUT IN TIME", font=_art_font(12, bold=True), fill=accent, anchor="mm")
        draw.text((64, 192), "WATCH IT", font=_art_font(19, bold=True), fill=(237, 233, 224))
        draw.text((64, 224), "RISE.", font=_art_font(19, bold=True), fill=(237, 233, 224))
        draw.text((64, 256), "THEN CASH OUT.", font=_art_font(17, bold=True), fill=(237, 233, 224))
    else:
        current_rank = current_rank or 8
        draw.text((64, 183), "PREDICT", font=_art_font(17, bold=True), fill=(181, 195, 205))
        draw.text((64, 214), "HIGHER OR LOWER", font=_art_font(16, bold=True), fill=(240, 236, 229))
        draw.text((64, 257), f"STREAK  /  {multiplier:.2f}x", font=_art_font(16, bold=True), fill=accent)
        def card(cx, rank, face_up=True):
            card_left, card_top = cx - 72, 56
            draw.rounded_rectangle((card_left + 7, card_top + 9, card_left + 151, card_top + 290), radius=18, fill=(3, 6, 12))
            draw.rounded_rectangle((card_left, card_top, card_left + 144, card_top + 282), radius=18, fill=(241, 239, 229), outline=(255, 255, 249), width=3)
            label = _hilo_rank_label(rank) if face_up else "?"
            color = (174, 42, 55) if rank in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14) else (25, 29, 36)
            draw.text((card_left + 17, card_top + 15), label, font=_art_font(26, bold=True), fill=color)
            draw.text((card_left + 72, card_top + 138), label, font=_art_font(58, bold=True), fill=color, anchor="mm")
            draw.text((card_left + 72, card_top + 230), "CKR", font=_art_font(14, bold=True), fill=(97, 103, 109), anchor="mm")
        if previous_rank is not None and final:
            card(545, previous_rank)
            card(775, current_rank)
            draw.text((660, 198), "›", font=_art_font(56, bold=True), fill=accent, anchor="mm")
        else:
            card(665, current_rank)
            draw.rounded_rectangle((888, 120, 1137, 300), radius=21, fill=(19, 24, 34), outline=accent, width=3)
            draw.text((1012, 158), "CURRENT CARD", font=_art_font(14, bold=True), fill=(177, 190, 195), anchor="mm")
            draw.text((1012, 208), _hilo_rank_label(current_rank), font=_art_font(40, bold=True), fill=(248, 245, 237), anchor="mm")
            draw.text((1012, 257), "HIGHER  /  LOWER", font=_art_font(13, bold=True), fill=accent, anchor="mm")

    if won is not None:
        badge_color = (48, 166, 112) if won else (170, 48, 58)
        draw.rounded_rectangle((58, 325, 314, 370), radius=13, fill=badge_color)
        draw.text((186, 347), "WIN" if won else "NO WIN", font=_art_font(19, bold=True), fill=(255, 255, 248), anchor="mm")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


class InteractiveSoloWagerView(DiagnosticViewMixin, LayoutView):
    """Base for multistep wagers whose escrow and final outcome are database-backed."""

    def __init__(self, solo_cog, ctx, amount: int, *, game_type: str, game_title: str, accent: int):
        super().__init__(timeout=180)
        self.cog = solo_cog
        self.context = ctx
        self.requester = ctx.author
        self.amount = int(amount)
        self.game_type = game_type
        self.game_title = game_title
        self.accent = accent
        self.game_id = uuid.uuid4().hex
        self.message = None
        self.done = False
        self.resolving = False
        self.receipt = None
        self.start_error_message = None
        self._wager_created = False
        self._controls = []
        self._state_lock = asyncio.Lock()
        self._settlement_task = None
        self._pending_display_result = None
        self._pending_resolution = None
        self._panel_text = TextDisplay("")
        self._art_filename = f"{game_type}.png"
        self._art_bytes = b""

    def _build(self, rows):
        self._refresh()
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

    def _content(self):
        if self.start_error_message:
            return f"# {self.game_title}\n{self.start_error_message}"
        if self.receipt is not None:
            return self._final_content()
        if self.resolving:
            return (
                f"# جاري تأكيد النتيجة · {self.game_title}\n"
                "قفلنا الاختيارات وبنثبت الرهان والرصيد في قاعدة البيانات. "
                "مش هيتكرر الخصم أو الصرف لو حصل تأخير."
            )
        return f"# {self.game_title}\n**الرهان:** {self.amount:,} {COIN}"

    def _final_content(self):
        receipt = self.receipt or {}
        outcome = str(receipt.get("outcome") or "")
        balance_after = receipt.get("balance_after")
        balance_line = f"\n**رصيدك:** {int(balance_after):,} {COIN}" if balance_after is not None else ""
        display = self._pending_display_result or str(receipt.get("result") or "")
        if receipt.get("status") == "refunded" or outcome == "balance_limit":
            return (
                f"# تم إرجاع الرهان · {self.game_title}\n"
                f"{display or 'الجولة اتوقفت بأمان؛ لم تُحتسب خسارة.'}\n"
                f"**المبلغ المُعاد:** {self.amount:,} {COIN}{balance_line}"
            )
        if outcome == "push":
            return (
                f"# تم استرداد الرهان · {self.game_title}\n"
                f"{display or 'تم إرجاع قيمة الرهان بالكامل.'}\n"
                f"**المبلغ المُعاد:** {self.amount:,} {COIN}{balance_line}"
            )
        if outcome == "win":
            payout = int(receipt.get("payout") or 0)
            profit = max(0, payout - self.amount)
            return (
                f"# كسبت · {self.game_title}\n{display}\n"
                f"**إجمالي الصرف:** {payout:,} {COIN} · **صافي المكسب:** {profit:,} {COIN}"
                f"{balance_line}"
            )
        return (
            f"# خسرت · {self.game_title}\n{display or 'انتهت الجولة من غير مكسب.'}"
            f"\n**الرهان المفقود:** {self.amount:,} {COIN}{balance_line}"
        )

    def _on_finished(self, receipt):
        """Update game-specific terminal controls before rendering the receipt."""

    def _set_controls_enabled(self, enabled: bool):
        for control in self._controls:
            control.disabled = not enabled

    def _prepare_refund(self, display_result: str):
        """Disable the panel and preserve a refund path even if artwork rendering fails."""
        self.resolving = True
        self._pending_display_result = display_result
        self._pending_resolution = {"force_refund": True}
        self._set_controls_enabled(False)
        try:
            self._refresh()
        except Exception:
            logger.exception("Could not render refund state game_id=%s", self.game_id)
            self._panel_text.content = (
                f"# جاري إرجاع الرهان · {self.game_title}\n{display_result}"
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

    async def start(self):
        """Publish a disabled board, escrow the stake with its panel reference, then enable it."""
        self._set_controls_enabled(False)
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
            logger.exception("Could not publish interactive wager game_type=%s user_id=%s", self.game_type, self.requester.id)
            raise

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
                "تم إيقاف التأكيد أثناء إنشاء الرهان؛ بنتحقق من العملية ثم نرجع المبلغ لو اتحجز."
            )
            try:
                await self._edit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Could not publish wager-creation cancellation game_id=%s",
                    self.game_id,
                )
            self._settlement_task = asyncio.create_task(
                self._settle_after_creation(creation_task)
            )
            raise
        except Exception:
            logger.exception("Could not confirm interactive wager creation game_id=%s", self.game_id)
            await self._queue_refund(
                "تعذر تأكيد إنشاء الرهان؛ بنتحقق من تسجيله ونرجعه لو اتحجز."
            )
            return False

        if not receipt.get("created"):
            balance = int(receipt.get("balance", 0))
            reason = receipt.get("reason")
            if reason == "insufficient_funds":
                self.start_error_message = f"رصيدك **{balance:,} {COIN}** ومش كفاية لرهان **{self.amount:,} {COIN}**."
            elif reason == "duplicate_game":
                self.start_error_message = "معرّف الجولة موجود بالفعل؛ لم يتم خصم رهان جديد."
            else:
                self.start_error_message = "تعذر قبول الرهان؛ لم يتم تسجيل خصم جديد."
            self.done = True
            self._set_controls_enabled(False)
            self._refresh()
            await self._edit()
            release_solo_session(self.requester.id, self)
            self.stop()
            return False

        self._wager_created = True
        try:
            self._set_controls_enabled(True)
            self._refresh()
            if not await self._edit():
                await self._queue_refund(
                    "تعذر تحديث لوحة الجولة بعد حجز الرهان؛ جارٍ إرجاع المبلغ."
                )
                return False
            await self._after_start()
            return True
        except asyncio.CancelledError:
            if not self.done and not self.resolving:
                self._prepare_refund(
                    "تم إيقاف تشغيل الجولة أثناء البدء؛ جارٍ إرجاع الرهان."
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
                    "حصل عطل أثناء تشغيل الجولة؛ تم طلب إرجاع الرهان بأمان."
                )
            return False

    async def _after_start(self):
        return None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "الجولة دي مش بتاعتك",
                    "كل لاعب يقدر يتحكم في جلسة الرهان اللي بدأها هو بس.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        if self.done or self.resolving:
            await interaction.response.send_message(
                view=StatusPanel("الجولة اتقفلت", "جارٍ تثبيت النتيجة أو انتهت الجولة بالفعل.", "warning"),
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
                    view=StatusPanel("الجولة اتقفلت", "النتيجة قيد التثبيت؛ مفيش إجراء تاني مطلوب.", "warning"),
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
        try:
            try:
                self._refresh()
            except Exception:
                logger.exception("Could not render terminal game state game_id=%s", self.game_id)
                self._prepare_refund(
                    "حصل عطل في تجهيز نتيجة الجولة؛ هنرجع الرهان بدل ما نحتسب نتيجة غير مؤكدة."
                )
            if interaction is not None:
                try:
                    await interaction.response.edit_message(view=self)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "Could not acknowledge interactive wager settlement game_id=%s",
                        self.game_id,
                        exc_info=True,
                    )
                    await self._edit()
            else:
                await self._edit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not publish terminal wager state game_id=%s", self.game_id)
        finally:
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
            try:
                self._on_finished(receipt)
                self._refresh()
            except Exception:
                logger.exception("Could not render settled wager panel game_id=%s", self.game_id)
                self._panel_text.content = self._final_content()
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
            self.stop()

    async def _finish_unrecorded(self):
        self.done = True
        self.resolving = False
        self.start_error_message = (
            "لم نقدر نلاقي سجل الرهان. لم نعلن مكسبًا غير مؤكد؛ راجع رصيدك "
            "قبل ما تبدأ جولة أخرى، ولو استمرت المشكلة ابعت للستاف."
        )
        self._set_controls_enabled(False)
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
        await self._begin_settlement(force_refund=True, display_result="انتهى الوقت؛ اترد الرهان بأمان.")

    async def on_timeout(self):
        if not self.done and not self.resolving:
            await self._on_timeout()


class MinesView(InteractiveSoloWagerView):
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
            game_title="Mines",
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
            label="إلغاء وإرجاع الرهان",
            emoji="↩️",
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
        if self.start_error_message or self.receipt is not None or self.resolving:
            return super()._content()
        multiplier = _mines_multiplier(self.mines_count, len(self.revealed)) if self.revealed else 1.0
        cashout = int(self.amount * multiplier) if self.revealed else self.amount
        return (
            f"# Mines · {self.mines_count} ألغام من 20\n"
            f"**الرهان:** {self.amount:,} {COIN} · **الخانات الآمنة:** {len(self.revealed)}\n"
            f"**مضاعف السحب الحالي:** {multiplier:.2f}x · **الصرف الحالي:** {cashout:,} {COIN}\n"
            "اكشف خانات آمنة لرفع المضاعف، أو اسحب قبل ما تفتح لغم. نسبة الصرف النظرية 97%."
        )

    def _on_finished(self, receipt):
        reveal_board = (
            receipt.get("outcome") in {"loss", "win", "push"}
            and receipt.get("status") != "refunded"
        )
        for index, button in self.tile_buttons.items():
            button.disabled = True
            if reveal_board and index in self.mines:
                button.emoji = "💣"
                button.style = discord.ButtonStyle.danger
            elif index in self.revealed:
                button.emoji = "💎"
                button.style = discord.ButtonStyle.success

    def _set_controls_enabled(self, enabled: bool):
        for index, button in self.tile_buttons.items():
            button.disabled = not enabled or index in self.revealed
        self.cashout_button.disabled = not enabled

    def _disable_tile(self, index, *, mine=False):
        button = self.tile_buttons[index]
        button.disabled = True
        if mine:
            button.emoji = "💣"
            button.style = discord.ButtonStyle.danger
        else:
            button.emoji = "💎"
            button.style = discord.ButtonStyle.success

    async def reveal(self, interaction, index: int):
        async with self._state_lock:
            if self.done or self.resolving or index in self.revealed:
                return
            if index in self.mines:
                self._pending_mine_hit = index
                await self._begin_settlement(
                    interaction,
                    result=f"mine:{index + 1}",
                    display_result=f"ظهر لغم في الخانة **{index + 1:02d}**.",
                    won=False,
                    payout=0,
                    outcome="loss",
                )
                return
            self.revealed.add(index)
            self._disable_tile(index)
            self.cashout_button.label = "سحب المكسب"
            self.cashout_button.emoji = "💰"
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
                    display_result=(
                        "حصل عطل في تحديث اللوحة؛ أوقفنا الجولة وهنرجع الرهان بدل "
                        "ما نحتسب نتيجة غير مؤكدة."
                    ),
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
                    display_result="انسحبت قبل فتح أي خانة؛ تم إرجاع الرهان بالكامل.",
                )
                return
            multiplier = _mines_multiplier(self.mines_count, len(self.revealed))
            payout = max(self.amount, int(self.amount * multiplier))
            outcome = "push" if payout <= self.amount else "win"
            await self._begin_settlement(
                interaction,
                result=f"cashout:{len(self.revealed)};multiplier={multiplier:.2f}x",
                display_result=(
                    f"سحبت بعد فتح **{len(self.revealed)}** خانات آمنة عند **{multiplier:.2f}x**."
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
                    display_result=f"انتهى الوقت؛ تم السحب تلقائيًا عند **{multiplier:.2f}x**.",
                    won=outcome == "win",
                    payout=payout if outcome == "win" else 0,
                    outcome=outcome,
                )
            else:
                await self._begin_settlement(
                    force_refund=True,
                    display_result="انتهى الوقت قبل فتح أي خانة؛ تم إرجاع الرهان.",
                )


class CrashView(InteractiveSoloWagerView):
    def __init__(self, solo_cog, ctx, amount: int):
        self.crash_point = _crash_point()
        self.started_at = None
        self._ticker_task = None
        self._last_drawn_multiplier = 1.0
        self.result_multiplier = None
        super().__init__(
            solo_cog,
            ctx,
            amount,
            game_type="crash",
            game_title="Crash",
            accent=THEME["red_dark"],
        )
        self.cashout_button = discord.ui.Button(
            label="سحب الآن · 1.00x",
            emoji="💸",
            style=discord.ButtonStyle.secondary,
            custom_id="crash_cashout",
            disabled=True,
        )
        self.cashout_button.callback = self.cashout
        self._controls = [self.cashout_button]
        self._build([ActionRow(self.cashout_button)])

    def _current_multiplier(self):
        if self.started_at is None:
            return 1.0
        elapsed = max(0.0, time.monotonic() - self.started_at)
        return min(CRASH_MAX_MULTIPLIER, math.exp(math.log(CRASH_GROWTH_PER_SECOND) * elapsed))

    def _render_art(self):
        current = self._current_multiplier()
        final = self.receipt is not None
        outcome = (
            self.receipt.get("outcome")
            if final and self.receipt.get("status") != "refunded"
            else None
        )
        crashed = outcome == "loss"
        shown_crash = self.crash_point if crashed else None
        return _render_live_art(
            "crash",
            multiplier=self.result_multiplier if final and self.result_multiplier is not None else current,
            elapsed=max(0.0, time.monotonic() - self.started_at) if self.started_at is not None else 0.0,
            crash_point=shown_crash,
            final=final,
            won=True if outcome == "win" else False if crashed else None,
        )

    def _content(self):
        if self.start_error_message or self.receipt is not None or self.resolving:
            return super()._content()
        current = self._current_multiplier()
        payout = int(self.amount * current)
        return (
            "# Crash · اسحب قبل الانهيار\n"
            f"**الرهان:** {self.amount:,} {COIN} · **المضاعف:** {current:.2f}x\n"
            f"**الصرف لو سحبت الآن:** {payout:,} {COIN}\n"
            "المضاعف بيزيد تلقائيًا. عند نقطة انهيار عشوائية، الرهان يضيع لو ما سحبتش. "
            "الحد الأقصى 100x."
        )

    async def _after_start(self):
        self.started_at = time.monotonic()
        self._refresh()
        await self._edit()
        if self.crash_point <= 1.0:
            self.result_multiplier = self.crash_point
            self._pending_resolution = {"display_multiplier": self.crash_point}
            await self._begin_settlement(
                result=f"crash:{self.crash_point:.2f}x",
                display_result=f"الانهيار حصل عند **{self.crash_point:.2f}x** قبل السحب.",
                won=False,
                payout=0,
                outcome="loss",
            )
            return
        self._ticker_task = asyncio.create_task(self._run_crash())

    async def _run_crash(self):
        try:
            while not self.done and not self.resolving:
                await asyncio.sleep(1.5)
                async with self._state_lock:
                    if self.done or self.resolving:
                        return
                    current = self._current_multiplier()
                    if current >= self.crash_point:
                        self.result_multiplier = self.crash_point
                        self._pending_resolution = {"display_multiplier": self.crash_point}
                        await self._begin_settlement(
                            result=f"crash:{self.crash_point:.2f}x",
                            display_result=f"الانهيار حصل عند **{self.crash_point:.2f}x**؛ الجولة انتهت قبل السحب.",
                            won=False,
                            payout=0,
                            outcome="loss",
                        )
                        return
                    self._last_drawn_multiplier = current
                    self.cashout_button.label = f"سحب الآن · {current:.2f}x"
                    self._refresh()
                    await self._edit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Crash ticker failed game_id=%s", self.game_id)
            if not self.done and not self.resolving:
                await self._begin_settlement(
                    result="crash_internal_error",
                    display_result="حصل عطل في تحديث الجولة؛ تم إيقافها بأمان وإرجاع الرهان.",
                    force_refund=True,
                )

    async def cashout(self, interaction):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            current = self._current_multiplier()
            if current >= self.crash_point:
                self.result_multiplier = self.crash_point
                self._pending_resolution = {"display_multiplier": self.crash_point}
                await self._begin_settlement(
                    interaction,
                    result=f"crash:{self.crash_point:.2f}x",
                    display_result=f"الانهيار حصل عند **{self.crash_point:.2f}x** قبل السحب.",
                    won=False,
                    payout=0,
                    outcome="loss",
                )
                return
            payout = max(self.amount, int(self.amount * current))
            outcome = "push" if payout <= self.amount else "win"
            self.result_multiplier = current
            self._pending_resolution = {"display_multiplier": current}
            await self._begin_settlement(
                interaction,
                result=f"cashout:{current:.2f}x",
                display_result=f"سحبت في الوقت المناسب عند **{current:.2f}x**.",
                won=outcome == "win",
                payout=payout if outcome == "win" else 0,
                outcome=outcome,
            )

    async def _on_timeout(self):
        async with self._state_lock:
            if self.done or self.resolving:
                return
            current = self._current_multiplier()
            if self.started_at is not None and current >= self.crash_point:
                self.result_multiplier = self.crash_point
                self._pending_resolution = {"display_multiplier": self.crash_point}
                await self._begin_settlement(
                    result=f"crash:{self.crash_point:.2f}x",
                    display_result=f"الانهيار حصل عند **{self.crash_point:.2f}x** قبل السحب.",
                    won=False,
                    payout=0,
                    outcome="loss",
                )
                return
            payout = max(self.amount, int(self.amount * current))
            outcome = "push" if payout <= self.amount else "win"
            self.result_multiplier = current
            self._pending_resolution = {"display_multiplier": current}
            await self._begin_settlement(
                result=f"timeout_cashout:{current:.2f}x",
                display_result=f"انتهى الوقت؛ تم السحب تلقائيًا عند **{current:.2f}x**.",
                won=outcome == "win",
                payout=payout if outcome == "win" else 0,
                outcome=outcome,
            )


class HiLoView(InteractiveSoloWagerView):
    def __init__(self, solo_cog, ctx, amount: int):
        self.current_rank = secrets.randbelow(13) + 2
        self.previous_rank = None
        self.streak = 0
        self.multiplier = 1.0
        self.last_guess = None
        super().__init__(
            solo_cog,
            ctx,
            amount,
            game_type="hilo",
            game_title="Hi-Lo",
            accent=THEME["teal"],
        )
        self.higher_button = discord.ui.Button(
            label="أعلى",
            emoji="⬆️",
            style=discord.ButtonStyle.secondary,
            custom_id="hilo_higher",
            disabled=True,
        )
        self.higher_button.callback = self.guess_higher
        self.lower_button = discord.ui.Button(
            label="أقل",
            emoji="⬇️",
            style=discord.ButtonStyle.secondary,
            custom_id="hilo_lower",
            disabled=True,
        )
        self.lower_button.callback = self.guess_lower
        self.cashout_button = discord.ui.Button(
            label="سحب الرهان",
            emoji="💰",
            style=discord.ButtonStyle.secondary,
            custom_id="hilo_cashout",
            disabled=True,
        )
        self.cashout_button.callback = self.cashout
        self._controls = [self.higher_button, self.lower_button, self.cashout_button]
        self._build([ActionRow(*self._controls)])

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
            multiplier=self.multiplier,
            final=self.receipt is not None,
            won=True if outcome == "win" else False if outcome == "loss" else None,
        )

    def _content(self):
        if self.start_error_message or self.receipt is not None or self.resolving:
            return super()._content()
        higher_count = 14 - self.current_rank
        lower_count = self.current_rank - 2
        return (
            f"# Hi-Lo · الكارت الحالي **{_hilo_rank_label(self.current_rank)}**\n"
            f"**الرهان:** {self.amount:,} {COIN} · **التخمينات الصحيحة:** {self.streak}\n"
            f"**المضاعف الحالي:** {self.multiplier:.2f}x · **الصرف الحالي:** {int(self.amount * self.multiplier):,} {COIN}\n"
            f"فرصة أعلى: **{higher_count}/13** · فرصة أقل: **{lower_count}/13**. "
            "التعادل يُحسب خسارة. اسحب في أي وقت لتثبيت المكسب."
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
            self.current_rank = next_rank
            self.last_guess = direction
            if not correct:
                await self._begin_settlement(
                    interaction,
                    result=f"wrong:{direction}:{previous}:{next_rank}",
                    display_result=(
                        f"توقعت **{'أعلى' if direction == 'higher' else 'أقل'}** من "
                        f"**{_hilo_rank_label(previous)}**، لكن ظهر **{_hilo_rank_label(next_rank)}**."
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
                    display_result=f"وصلت للحد الأقصى بعد **{self.streak}** تخمينات صحيحة؛ تم السحب تلقائيًا عند **{self.multiplier:.2f}x**.",
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
                        "حصل عطل في تحديث الكارت؛ أوقفنا الجولة وهنرجع الرهان "
                        "بدل ما نعرض نتيجة غير مؤكدة."
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
                    "سحبت قبل أول تخمين؛ تم استرداد الرهان."
                    if self.streak == 0
                    else f"سحبت بعد **{self.streak}** تخمينات صحيحة عند **{self.multiplier:.2f}x**."
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
                    "انتهى الوقت قبل التخمين؛ تم استرداد الرهان."
                    if self.streak == 0
                    else f"انتهى الوقت؛ تم السحب تلقائيًا عند **{self.multiplier:.2f}x**."
                ),
                won=outcome == "win",
                payout=payout if outcome == "win" else 0,
                outcome=outcome,
            )


PLINKO_WEIGHTS = (1, 8, 28, 56, 70, 56, 28, 8, 1)
PLINKO_MULTIPLIERS = (3.0, 2.0, 1.5, 1.1, 0.0, 1.1, 1.5, 2.0, 3.0)


def _plinko_drop(_choice: str, stake: int) -> tuple[str, bool, int]:
    """Resolve one weighted Plinko ball; low-return center slot loses the stake."""
    if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
        raise ValueError("Plinko stake must be a positive integer")
    roll = secrets.randbelow(sum(PLINKO_WEIGHTS))
    slot = 0
    for index, weight in enumerate(PLINKO_WEIGHTS):
        if roll < weight:
            slot = index
            break
        roll -= weight
    multiplier = PLINKO_MULTIPLIERS[slot]
    result = f"slot={slot};multiplier={multiplier:g}x"
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
                    "اللعبة غير متاحة مؤقتًا",
                    "نظام تسوية الرهانات بيتحقق من قاعدة البيانات. جرّب تاني بعد لحظات؛ مفيش رهان هيتقبل قبل التأكد من أمان الرصيد.",
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
                    "قيمة الرهان مش مناسبة",
                    f"استخدم {usage}. أقل رهان **{MIN_BET:,}** وأقصى رهان **{MAX_BET:,}** {COIN}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        if has_active_solo_session(ctx.author.id):
            return await ctx.send(
                view=StatusPanel(
                    "عندك جلسة مفتوحة",
                    "كمّل أو اسحب من جلسة الرهان الحالية قبل ما تبدأ جولة جديدة.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        view = view_type(solo, ctx, amount, *view_args)
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel(
                    "عندك جلسة مفتوحة",
                    "كمّل أو اسحب من جلسة الرهان الحالية قبل ما تبدأ جولة جديدة.",
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
        aliases=["الألغام", "الغام", "minesweeper"],
        description="ابدأ Mines، اكشف خانات آمنة واسحب قبل العثور على لغم",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def mines(self, ctx, bet: str, mines_count: int = 3):
        if not 1 <= mines_count <= MINES_MAX_COUNT:
            return await ctx.send(
                view=StatusPanel(
                    "عدد ألغام غير صالح",
                    "اختار من 1 إلى 8 ألغام. كل ما زاد عدد الألغام زادت المخاطرة والمضاعفات.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        return await self._start_interactive(ctx, bet, MinesView, mines_count)

    @commands.hybrid_command(
        name="crash",
        aliases=["كراش"],
        description="راهن في Crash واسحب قبل انهيار المضاعف",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def crash(self, ctx, bet: str):
        return await self._start_interactive(ctx, bet, CrashView)

    @commands.hybrid_command(
        name="hilo",
        aliases=["hi-lo", "highlow", "هاي_لو"],
        description="خمّن الكارت التالي أعلى أم أقل واسحب مضاعفك في أي وقت",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def hilo(self, ctx, bet: str):
        return await self._start_interactive(ctx, bet, HiLoView)

    @commands.hybrid_command(
        name="plinko",
        aliases=["بلينكو"],
        description="أسقط كرة Plinko وشاهد الخانة والمضاعف الذي وصلت إليه",
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
            choices={"drop": {"label": "إسقاط الكرة", "emoji": "🔽"}},
            payout_numerator=100,
            odds_description="احتمالات الخانات موزونة حسب توزيع 8 صفوف.",
            instructions="اضغط إسقاط الكرة؛ المضاعفات مكتوبة على الخانات أسفل اللوحة.",
            command_name="plinko",
            payout_description=(
                "المضاعفات من اليسار لليمين: 3x، 2x، 1.5x، 1.1x، 0x، "
                "1.1x، 1.5x، 2x، 3x. نسبة الصرف النظرية حوالي 95.8%."
            ),
            outcome_resolver=_plinko_drop,
        )

    @commands.hybrid_command(
        name="numberroulette",
        aliases=["number-roulette", "روليت_الأرقام", "روليت_ارقام", "روليت_الارقام"],
        description="راهن على رقم من 0 إلى 36 في الروليت الأوروبية",
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
            game_title="روليت الأرقام",
            choices=choices,
            payout_numerator=3600,
            odds_description="رقم واحد من 37 رقمًا؛ صرف الرقم الصحيح 36x شاملًا الرهان.",
            instructions="اختار رقمًا من القائمة 0–18 أو 19–36، ثم اضغط تأكيد الاختيار.",
            command_name="numberroulette",
            payout_description=(
                "روليت أوروبية بصفر واحد. اختيار الرقم الصحيح احتماله 1 من 37 "
                "وإجمالي الصرف 36x، بنسبة صرف نظرية 97.30%."
            ),
            outcome_resolver=_number_roulette,
        )


async def setup(bot):
    await bot.add_cog(AdvancedSoloGames(bot))
