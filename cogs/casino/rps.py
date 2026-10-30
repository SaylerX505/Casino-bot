import asyncio
import logging
import time
import uuid
from functools import partial

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.game_registry import (
    is_active_game,
    register_game_task,
    release_game_task,
)
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    developer_credit_row,
    base_embed,
    embed_to_v2,
    get_currency,
    send_status,
)

COIN = "<:ckrcoin:1513743032124575784>"
MOVE_NAMES = {"🪨": "حجر", "📄": "ورقة", "✂️": "مقص"}
BEATS = {"🪨": "✂️", "📄": "🪨", "✂️": "📄"}
MIN_BET = 10
MAX_BET = 5000
PAYOUT_RATE = 0.95


async def _settle_wager(game_id, winner_id=None):
    """Settle idempotently, retrying transient database failures before failing closed."""
    logger = logging.getLogger(__name__)
    for attempt in range(3):
        try:
            return await asyncio.to_thread(bank.settle_rps_wager, game_id, winner_id)
        except Exception:
            logger.exception(
                "Failed to settle persisted RPS wager %s (attempt %s/3)",
                game_id,
                attempt + 1,
            )
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
    return {"settled": False, "reason": "database_error"}



_RECOVERY_DELAYS_SECONDS = (5, 15, 30, 60, 120, 300)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}
_STARTUP_PANEL_TASKS: set[asyncio.Task] = set()


async def _publish_interrupted_rps_panel(bot, record, receipt):
    """Close a stale challenge panel only after both stakes are safely refunded."""
    if receipt.get("status") != "refunded":
        return
    if receipt.get("outcome") != "refund":
        return
    channel_id = record.get("channel_id")
    message_id = record.get("message_id")
    if not channel_id or not message_id:
        return

    amount = int(record.get("amount", 0))
    total_refund = amount * 2
    try:
        await bot.wait_until_ready()
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            view=StatusPanel(
                "التحدي اتوقف بعد إعادة التشغيل",
                (
                    "التحدي السابق ماكملش قبل ما البوت يقفل. "
                    f"اترد **{amount:,} {COIN} لكل لاعب** "
                    f"(إجمالي **{total_refund:,} {COIN}** للطرفين). "
                    "الأزرار القديمة اتقفلت؛ ابدأوا تحديًا جديدًا لو حابين."
                ),
                "warning",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logging.getLogger(__name__).info(
            "Interrupted RPS wager refunded but original panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logging.getLogger(__name__).warning(
            "Interrupted RPS wager refunded but the bot cannot access its panel game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logging.getLogger(__name__).warning(
            "Interrupted RPS wager refunded but its old panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Interrupted RPS wager was refunded, but unexpected panel cleanup failed game_id=%s",
            record.get("game_id"),
        )


async def _publish_expired_rps_challenge_panel(bot, record):
    """Close a pre-acceptance invite after restart; no stake was ever reserved."""
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
        await message.edit(
            view=StatusPanel(
                "انتهى التحدي بعد إعادة التشغيل",
                "التحدي ما اتقبلش قبل ما البوت يعيد التشغيل، فتم إقفاله من غير حجز أو خصم أي عملات. ابدأوا تحديًا جديدًا لو حابين.",
                "warning",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logging.getLogger(__name__).info(
            "Expired unaccepted RPS challenge panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logging.getLogger(__name__).warning(
            "Expired unaccepted RPS challenge could not be closed due to channel permissions game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logging.getLogger(__name__).warning(
            "Expired unaccepted RPS challenge but its panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )


async def _retry_wager_recovery(game_id, winner_id=None, on_recovered=None):
    """Keep retrying unresolved escrow while this process is alive.

    The bank transition is idempotent. If the process shuts down, startup recovery
    refunds any remaining active escrow, so retrying cannot double-pay a wager.
    """
    game_id = str(game_id)
    logger = logging.getLogger(__name__)
    attempt = 0
    try:
        while True:
            delay = _RECOVERY_DELAYS_SECONDS[min(attempt, len(_RECOVERY_DELAYS_SECONDS) - 1)]
            await asyncio.sleep(delay)
            result = await _settle_wager(game_id, winner_id)
            if result.get("settled"):
                logger.info(
                    "Recovered pending RPS wager game_id=%s status=%s",
                    game_id,
                    result.get("status"),
                )
                if on_recovered is not None:
                    try:
                        await on_recovered(result)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Could not refresh the recovered RPS wager message")
                return
            if result.get("reason") == "not_found":
                logger.critical(
                    "Cannot recover RPS wager because its escrow record is missing game_id=%s",
                    game_id,
                )
                return

            attempt += 1
            if attempt % 3 == 0:
                logger.error(
                    "RPS wager remains escrowed after recovery attempts=%s game_id=%s reason=%s",
                    attempt,
                    game_id,
                    result.get("reason", "unknown"),
                )
    finally:
        current = asyncio.current_task()
        if _RECOVERY_TASKS.get(game_id) is current:
            _RECOVERY_TASKS.pop(game_id, None)


def _schedule_wager_recovery(game_id, winner_id=None, on_recovered=None):
    """Schedule at most one background recovery worker per wager."""
    key = str(game_id)
    existing = _RECOVERY_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_retry_wager_recovery(key, winner_id, on_recovered))
    _RECOVERY_TASKS[key] = task
    return task


# --- CHALLENGE VIEW ---
class RPSView(DiagnosticViewMixin, LayoutView):
    """A consent-first duel: reserve both stakes only after the invited player accepts."""

    def __init__(self, ctx, player, opponent, bet, game_id):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.player = player
        self.opponent = opponent
        self.bet = bet
        self.game_id = str(game_id)
        self.choices = {}
        self._choice_lock = asyncio.Lock()
        self.accepted = False
        self.resolving = False
        self.done = False
        self.message = None
        self.deadline = None
        self._deadline_task = None
        self.result_published = False
        self._show_developer_credit = False
        self._panel_text = TextDisplay(self._embed_content(self.make_embed()))
        self._buttons = []
        self._render_view()

    def schedule_deadline(self, timeout: float = 60.0):
        """Start an absolute gameplay deadline unaffected by unauthorized clicks."""
        timeout = max(0.0, float(timeout))
        self.deadline = time.monotonic() + timeout
        existing = self._deadline_task
        if existing is not None and not existing.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if existing is not current:
                existing.cancel()
        self._deadline_task = asyncio.create_task(self._enforce_deadline(self.deadline))
        return self.deadline

    async def _enforce_deadline(self, deadline: float):
        try:
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))
            if self.deadline == deadline and not self.done:
                await self.finish()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception(
                "RPS absolute deadline handler failed game_id=%s",
                self.game_id,
            )
            # If a deadline callback itself fails, never leave accepted escrow
            # unattended. Settlement is idempotent; recovery retries safely.
            if self.accepted:
                _schedule_wager_recovery(
                    self.game_id,
                    on_recovered=self._publish_recovered_settlement,
                )
            else:
                try:
                    await asyncio.to_thread(bank.expire_rps_challenge, self.game_id)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not close expired RPS invite after watchdog error game_id=%s",
                        self.game_id,
                    )
        finally:
            if self._deadline_task is asyncio.current_task():
                self._deadline_task = None

    def stop(self):
        task = self._deadline_task
        self._deadline_task = None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()
        return super().stop()

    def make_embed(self):
        prize = int(self.bet * 2 * PAYOUT_RATE)
        if not self.accepted:
            return base_embed(
                title="✊ تحدي حومص",
                description=(
                    f"{self.player.mention} تحدّى {self.opponent.mention}\n"
                    f"**الرهان:** {self.bet:,} لكل لاعب · **الجائزة:** {prize:,}\n"
                    "**بانتظار القبول · المهلة:** 60 ثانية"
                ),
                color=THEME["teal"],
            )

        player_status = "✅ اختار" if self.player.id in self.choices else "⏳ مستني"
        opponent_status = "✅ اختار" if self.opponent.id in self.choices else "⏳ مستني"
        player_name = getattr(self.player, "display_name", self.player.mention)
        opponent_name = getattr(self.opponent, "display_name", self.opponent.mention)
        return base_embed(
            title="✊ حومص · الجولة بدأت",
            description=(
                f"{self.player.mention} **VS** {self.opponent.mention}\n"
                f"**الرهان:** {self.bet:,} لكل لاعب · **الجائزة:** {prize:,}\n"
                f"**الاختيارات:** {player_name} — {player_status} · {opponent_name} — {opponent_status}"
            ),
            color=THEME["teal"],
        )

    def _render_view(self):
        self.clear_items()
        self._buttons = []
        if not self.done:
            if self.accepted:
                actions = (
                    ("🪨", "حجر", "rock"),
                    ("📄", "ورقة", "paper"),
                    ("✂️", "مقص", "scissors"),
                )
            else:
                actions = (
                    ("✅", "قبول التحدي", "accept_challenge"),
                    ("✖️", "رفض", "decline_challenge"),
                    ("↩️", "إلغاء التحدي", "cancel_challenge"),
                )
            for emoji, label, callback_name in actions:
                button = discord.ui.Button(
                    emoji=emoji,
                    label=label,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"rps_{callback_name}",
                )
                button.callback = partial(getattr(self, callback_name), button=button)
                self._buttons.append(button)

        components = [self._panel_text, Separator()]
        if self._buttons:
            components.append(ActionRow(*self._buttons))
        if self._show_developer_credit:
            components.extend([Separator(), developer_credit_row()])
        self.add_item(
            Container(
                *components,
                accent_color=getattr(self, "_panel_accent", THEME["green"] if self.accepted else THEME["teal"]),
            )
        )

    @staticmethod
    def _embed_content(embed):
        parts = []
        if embed.title:
            parts.append(f"# {embed.title}")
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f"**{field.name}**\n{field.value}")
        if embed.footer and embed.footer.text:
            parts.append(f"-# {embed.footer.text}")
        return "\n\n".join(parts)[:4000]

    def _refresh_panel(self, embed=None, *, developer_credit=False):
        self._show_developer_credit = developer_credit
        embed = embed or self.make_embed()
        self._panel_text.content = self._embed_content(embed)
        color = getattr(embed, "color", None)
        self._panel_accent = (
            color.value
            if color is not None
            else (THEME["green"] if self.accepted else THEME["teal"])
        )
        self._render_view()

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _private_notice(self, interaction, content):
        """Show private feedback using the same Components V2 status layout as other flows."""
        panel = StatusPanel("تحديث التحدي", content, "neutral")
        if interaction.response.is_done():
            return await interaction.edit_original_response(
                content=None,
                embeds=[],
                attachments=[],
                view=panel,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return await interaction.response.send_message(
            view=panel,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _defer_private(self, interaction) -> bool:
        if interaction.response.is_done():
            return True
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logging.getLogger(__name__).warning(
                "Could not acknowledge RPS challenge action game_id=%s user_id=%s",
                self.game_id,
                interaction.user.id,
                exc_info=True,
            )
            return False
        return True

    async def _store_panel_reference(self):
        if self.message is None or not getattr(self.message, "id", None):
            return
        channel_id = int(self.ctx.channel.id)
        message_id = int(self.message.id)
        try:
            await asyncio.to_thread(
                bank.set_rps_challenge_message_reference,
                self.game_id,
                channel_id,
                message_id,
            )
            if self.accepted:
                await asyncio.to_thread(
                    bank.set_rps_wager_message_reference,
                    self.game_id,
                    channel_id,
                    message_id,
                )
        except Exception:
            # A missing optional message reference must never change money state.
            logging.getLogger(__name__).exception(
                "Could not persist RPS challenge panel reference game_id=%s",
                self.game_id,
            )

    async def _update_live_panel(self) -> bool:
        """Update the original panel; only publish a replacement if Discord confirms deletion."""
        if self.message is not None:
            for attempt in range(2):
                try:
                    await self.message.edit(view=self)
                    await self._store_panel_reference()
                    return True
                except discord.NotFound:
                    logging.getLogger(__name__).info(
                        "RPS challenge panel was deleted; publishing current state again game_id=%s",
                        self.game_id,
                    )
                    self.message = None
                    break
                except discord.HTTPException as error:
                    status = getattr(error, "status", 0)
                    transient = status == 429 or status >= 500
                    if transient and attempt == 0:
                        logging.getLogger(__name__).warning(
                            "Transient RPS panel edit failure; retrying the same message game_id=%s status=%s",
                            self.game_id,
                            status,
                            exc_info=True,
                        )
                        await asyncio.sleep(0.25)
                        continue
                    logging.getLogger(__name__).warning(
                        "Could not update RPS challenge panel; keeping the existing message instead of posting a duplicate game_id=%s status=%s",
                        self.game_id,
                        status,
                        exc_info=True,
                    )
                    return False

        # A replacement is safe only when the original message was confirmed missing,
        # or when this game has not published a panel yet. Generic edit failures are
        # deliberately not treated as deletion because that used to duplicate panels.
        try:
            self.message = await self.ctx.send(
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._store_panel_reference()
            return True
        except discord.HTTPException:
            logging.getLogger(__name__).exception(
                "Could not publish the current RPS challenge state game_id=%s",
                self.game_id,
            )
            return False

    async def accept_challenge(self, interaction: discord.Interaction, button=None):
        if not await self._defer_private(interaction):
            return

        async with self._choice_lock:
            if self.done:
                return await self._private_notice(interaction, "التحدي خلص بالفعل.")
            if interaction.user.id != self.opponent.id:
                return await self._private_notice(interaction, "الخصم المحدد في التحدي فقط هو اللي يقدر يقبله.")
            if (
                not self.accepted
                and self.deadline is not None
                and time.monotonic() >= self.deadline
            ):
                return await self._private_notice(
                    interaction,
                    "انتهت مهلة القبول؛ الدعوة هتتقفل بأمان ومش هيتحجز أي رصيد.",
                )
            if self.accepted:
                return await self._private_notice(interaction, "التحدي اتقبل بالفعل؛ اختار حركتك من لوحة الجولة.")
            if self.resolving:
                return await self._private_notice(interaction, "جاري تأكيد حالة التحدي؛ استنى لحظة وجرب تاني.")

            self.resolving = True
            try:
                receipt = await asyncio.to_thread(
                    bank.accept_rps_challenge,
                    self.game_id,
                    interaction.user.id,
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not confirm RPS challenge acceptance game_id=%s",
                    self.game_id,
                )
                try:
                    status = await asyncio.to_thread(bank.rps_challenge_status, self.game_id)
                except Exception:
                    status = None
                    logging.getLogger(__name__).exception(
                        "Could not verify RPS challenge after acceptance error game_id=%s",
                        self.game_id,
                    )
                if status is None or status.get("status") != "accepted":
                    self.resolving = False
                    return await self._private_notice(
                        interaction,
                        "تعذر تأكيد القبول. مش هنعلن إن الجولة بدأت من غير ما نتأكد من حالة الرهان؛ جرّب تاني لو التحدي لسه مفتوح.",
                    )
                receipt = {"accepted": True, "already_accepted": True, "status": "accepted"}

            if not receipt.get("accepted"):
                reason = receipt.get("reason", "unknown")
                self.resolving = False
                if reason == "not_authorized":
                    return await self._private_notice(interaction, "الخصم المحدد فقط هو اللي يقدر يقبل التحدي.")
                if reason == "insufficient_funds":
                    self.done = True
                    public_notice = (
                        "واحد من الطرفين رصيده أقل من قيمة الرهان. اتقفل التحدي من غير خصم أي عملات؛ "
                        "زوّدوا الرصيد وافتحوا تحديًا جديدًا."
                    )
                    self._refresh_panel(
                        base_embed(
                            title="تعذر بدء التحدي",
                            description=public_notice,
                            color=THEME["orange"],
                        )
                    )
                    await self._update_live_panel()
                    self.stop()
                    if receipt.get("player_b_balance", 0) < receipt.get("amount", self.bet):
                        private_notice = (
                            f"رصيدك الحالي **{int(receipt.get('player_b_balance', 0)):,}** {COIN}، "
                            f"والرهان المطلوب **{self.bet:,}**. مفيش عملات اتخصمت."
                        )
                    else:
                        private_notice = (
                            f"رصيد {self.player.mention} مش كفاية لتحدي بقيمة **{self.bet:,}**. "
                            "مفيش عملات اتخصمت من أي طرف."
                        )
                    return await self._private_notice(interaction, private_notice)

                self.done = True
                self._refresh_panel(
                    base_embed(
                        title="التحدي لم يعد متاحًا",
                        description="تم إغلاق الدعوة أو انتهت صلاحيتها قبل قبولها. مفيش رهان اتخصم.",
                        color=THEME["orange"],
                    )
                )
                await self._update_live_panel()
                self.stop()
                return await self._private_notice(interaction, "التحدي لم يعد مفتوحًا، ومفيش رهان اتخصم.")

            self.accepted = True
            self.resolving = False
            # Acceptance starts a fresh, fixed round-selection window. Later
            # component activity cannot extend it indefinitely.
            self.schedule_deadline(60.0)
            self._refresh_panel()
            await self._update_live_panel()
            return await self._private_notice(
                interaction,
                f"✅ قبلت التحدي. اتحجز **{self.bet:,} {COIN}** من رصيد كل طرف؛ اختار حركتك بسرية من اللوحة العامة.",
            )

    async def decline_challenge(self, interaction: discord.Interaction, button=None):
        await self._close_invitation(interaction, action="decline")

    async def cancel_challenge(self, interaction: discord.Interaction, button=None):
        await self._close_invitation(interaction, action="cancel")

    async def _close_invitation(self, interaction: discord.Interaction, *, action: str):
        if not await self._defer_private(interaction):
            return

        async with self._choice_lock:
            if self.done:
                return await self._private_notice(interaction, "التحدي اتقفل بالفعل.")
            if self.accepted:
                return await self._private_notice(
                    interaction,
                    "التحدي اتقبل والرهان اتحجز بالفعل؛ مينفعش يتلغى من زر الدعوة.",
                )

            expected_user = self.opponent.id if action == "decline" else self.player.id
            if interaction.user.id != expected_user:
                message = (
                    "الخصم المحدد فقط يقدر يرفض التحدي."
                    if action == "decline"
                    else "صاحب التحدي الأصلي فقط يقدر يلغيه."
                )
                return await self._private_notice(interaction, message)
            if self.deadline is not None and time.monotonic() >= self.deadline:
                return await self._private_notice(
                    interaction,
                    "انتهت مهلة الدعوة؛ مش هنقبل تغيير حالتها بعد الوقت المحدد.",
                )

            operation = bank.decline_rps_challenge if action == "decline" else bank.cancel_rps_challenge
            try:
                receipt = await asyncio.to_thread(operation, self.game_id, interaction.user.id)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not close RPS invite game_id=%s action=%s",
                    self.game_id,
                    action,
                )
                try:
                    status = await asyncio.to_thread(bank.rps_challenge_status, self.game_id)
                except Exception:  # noqa: BLE001 - persistence verification must fail closed.
                    status = None
                target_status = "declined" if action == "decline" else "cancelled"
                if status is None or status.get("status") != target_status:
                    return await self._private_notice(
                        interaction,
                        "تعذر تأكيد إغلاق التحدي. مفيش رهان اتخصم؛ حاول تاني بعد لحظة.",
                    )
                receipt = {"closed": True, "status": target_status}

            if not receipt.get("closed"):
                if receipt.get("reason") == "not_authorized":
                    return await self._private_notice(interaction, "مش مسموح لك تنفذ الإجراء ده على التحدي.")
                if receipt.get("reason") == "accepted":
                    self.accepted = True
                    self._refresh_panel()
                    await self._update_live_panel()
                    return await self._private_notice(interaction, "التحدي اتقبل بالفعل؛ استخدم أزرار الجولة.")
                return await self._private_notice(interaction, "التحدي اتقفل أو انتهت صلاحيته بالفعل.")

            self.done = True
            title = "تم رفض التحدي" if action == "decline" else "تم إلغاء التحدي"
            description = (
                f"{self.opponent.mention} رفض الدعوة. مفيش رصيد اتحجز."
                if action == "decline"
                else f"{self.player.mention} ألغى الدعوة قبل القبول. مفيش رصيد اتحجز."
            )
            self._refresh_panel(
                base_embed(title=title, description=description, color=THEME["grey"])
            )
            await self._update_live_panel()
            self.stop()
            return await self._private_notice(interaction, title + ". مفيش أي عملات اتخصمت.")

    def _queue_recovery_if_needed(self, result, winner_id=None):
        if result.get("settled"):
            return
        if result.get("reason") not in {"database_error", "balance_limit"}:
            return
        _schedule_wager_recovery(
            self.game_id,
            winner_id,
            self._publish_recovered_settlement,
        )

    async def _recover_after_component_error(self, incident_id: str) -> None:
        """Close a failed invite without escrow, or refund an accepted interrupted wager."""
        async with self._choice_lock:
            if self.done:
                return
            self.done = True
            accepted = self.accepted
            self._disable_buttons()

        if not accepted:
            try:
                await asyncio.to_thread(bank.expire_rps_challenge, self.game_id)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not expire failed unaccepted RPS challenge game_id=%s",
                    self.game_id,
                )
            self._refresh_panel(
                base_embed(
                    title="⚠️ التحدي اتقفل بأمان",
                    description=(
                        f"حصل عطل أثناء التفاعل. رقم المتابعة: **{incident_id}**.\n"
                        "التحدي لم يكن مقبولًا؛ مفيش أي رصيد اتحجز أو اتخصم."
                    ),
                    color=THEME["orange"],
                )
            )
            await self._update_live_panel()
            self.stop()
            return

        result = await _settle_wager(self.game_id)
        if result.get("settled"):
            await self._publish_recovered_settlement(result, incident_id=incident_id)
        else:
            self._refresh_panel(
                base_embed(
                    title="⚠️ التحدي متوقف مؤقتًا",
                    description=(
                        f"حصل عطل أثناء التفاعل. رقم المتابعة: **{incident_id}**.\n"
                        "الرهان لسه محفوظ؛ هنحاول إرجاعه بأمان، ومش هنحسب نتيجة غير مؤكدة."
                    ),
                    color=THEME["orange"],
                )
            )
            if self.message is not None:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    logging.getLogger(__name__).warning(
                        "Could not show interrupted RPS challenge incident_id=%s",
                        incident_id,
                        exc_info=True,
                    )
            if result.get("reason") != "not_found":
                _schedule_wager_recovery(
                    self.game_id,
                    None,
                    partial(self._publish_recovered_settlement, incident_id=incident_id),
                )
            self.stop()

    async def _publish_recovered_settlement(self, result, *, incident_id=None):
        if self.message is None:
            return
        if result.get("status") == "settled":
            winner_id = result.get("winner_id")
            if winner_id is None:
                description = "تم التأكد أن الرهان اتسوى بالفعل. لم نكرر أي خصم أو صرف."
            else:
                balance = await asyncio.to_thread(bank.get_balance, int(winner_id))
                description = (
                    f"اكتملت التسوية بنجاح. الفائز: <@{winner_id}>.\n"
                    f"**رصيدك:** {balance:,} {COIN}"
                )
        elif result.get("status") == "refunded":
            description = "اكتملت التسوية بإرجاع الرهان للطرفين بالكامل."
        else:
            description = "تمت معالجة الرهان بأمان."
        if incident_id:
            description += f"\n-# رقم المتابعة: {incident_id}"
        self._refresh_panel(
            base_embed(
                title="✅ تم تحديث حالة الرهان",
                description=description,
                color=THEME["teal"],
            )
        )
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            logging.getLogger(__name__).info(
                "Recovered RPS wager after its original message was deleted game_id=%s",
                self.game_id,
            )
        except discord.HTTPException:
            logging.getLogger(__name__).warning(
                "Recovered RPS wager but could not update its message game_id=%s",
                self.game_id,
                exc_info=True,
            )

    async def publish_result(self, embed, interaction=None):
        """Close the challenge board and publish a separate V2 receipt when possible."""
        self.done = True
        deferred_private_ack = interaction is not None and interaction.response.is_done()
        # Build the complete result first. It stays available as a fallback until
        # Discord confirms the separate receipt was actually sent.
        self._refresh_panel(embed, developer_credit=True)

        board_already_updated = False
        try:
            # Acknowledge a still-open component interaction promptly. Deferred
            # actions can wait for receipt delivery before updating the public board.
            if interaction is not None and not deferred_private_ack:
                try:
                    await interaction.response.edit_message(view=self)
                    self.message = interaction.message or self.message
                    board_already_updated = True
                except discord.NotFound:
                    try:
                        self.message = await self.ctx.send(
                            view=self,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        board_already_updated = True
                    except discord.HTTPException:
                        logging.getLogger(__name__).exception(
                            "Could not recreate closed RPS board game_id=%s",
                            self.game_id,
                        )
                except discord.HTTPException:
                    logging.getLogger(__name__).exception(
                        "Could not acknowledge final RPS board update game_id=%s",
                        self.game_id,
                    )

            if not self.result_published:
                try:
                    await self.ctx.send(
                        view=embed_to_v2(embed, developer_credit=True),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.result_published = True
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not publish separate RPS result receipt game_id=%s",
                        self.game_id,
                    )

            if self.result_published:
                # Normal UX: the full result has a dedicated message; the
                # original challenge becomes a compact, closed history panel.
                self._panel_text.content = (
                    "# انتهى تحدي حومص\n"
                    f"**الحالة النهائية:** {embed.title or 'التحدي انتهى'}\n"
                    "اتقفلت أزرار التحدي. تفاصيل النتيجة والرهان موجودة في إيصال منفصل بالقناة."
                )
                color = getattr(embed, "color", None)
                if color is not None:
                    self._panel_accent = color.value
                self._show_developer_credit = False
                self._render_view()

            # On a failed receipt send, keep/render the full result on the board;
            # never leave a teaser claiming a message exists when it does not.
            if not board_already_updated or self.result_published:
                try:
                    if self.message is not None:
                        await self.message.edit(view=self)
                    else:
                        self.message = await self.ctx.send(
                            view=self,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                except discord.NotFound:
                    try:
                        self.message = await self.ctx.send(
                            view=self,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.HTTPException:
                        logging.getLogger(__name__).exception(
                            "Could not publish closed RPS board fallback game_id=%s",
                            self.game_id,
                        )
                except discord.HTTPException:
                    logging.getLogger(__name__).warning(
                        "Could not update closed RPS board after receipt attempt game_id=%s",
                        self.game_id,
                        exc_info=True,
                    )

            if deferred_private_ack:
                if self.result_published:
                    message = "انتهت الجولة؛ إيصال النتيجة النهائية موجود في رسالة عامة منفصلة بالقناة."
                else:
                    message = "انتهت الجولة؛ تفاصيل النتيجة والرهان موجودة على لوحة التحدي، لكن تعذر إرسال الإيصال المنفصل."
                try:
                    await interaction.edit_original_response(
                        content=None,
                        embeds=[],
                        attachments=[],
                        view=StatusPanel(
                            "تم تسجيل اختيارك",
                            message,
                            "success" if self.result_published else "warning",
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logging.getLogger(__name__).warning(
                        "Could not close deferred RPS acknowledgement game_id=%s",
                        self.game_id,
                        exc_info=True,
                    )
        finally:
            self.stop()

    async def finish(self, interaction=None):
        # Serialize terminal state with acceptance and choice changes. A timeout
        # may win this race after the final click has already deferred its reply;
        # always close that deferred acknowledgement rather than leave it loading.
        async with self._choice_lock:
            if self.done:
                already_done = True
                accepted = self.accepted
                player_choice = self.choices.get(self.player.id)
                opponent_choice = self.choices.get(self.opponent.id)
            else:
                already_done = False
                self.done = True
                accepted = self.accepted
                player_choice = self.choices.get(self.player.id)
                opponent_choice = self.choices.get(self.opponent.id)

        if already_done:
            if interaction is not None:
                return await self._private_notice(
                    interaction,
                    "الجولة اتقفلت بالفعل؛ النتيجة النهائية ظاهرة في لوحة التحدي.",
                )
            return

        if not accepted:
            try:
                await asyncio.to_thread(bank.expire_rps_challenge, self.game_id)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not persist expired unaccepted RPS challenge game_id=%s",
                    self.game_id,
                )
            embed = base_embed(
                title="⏰ انتهى وقت قبول التحدي",
                description=(
                    "انتهى الوقت من غير ما الخصم يقبل الدعوة. اتقفل التحدي، "
                    "ومفيش رصيد اتحجز أو اتخصم من أي طرف."
                ),
                color=THEME["grey"],
            )
            return await self.publish_result(embed, interaction)

        if player_choice is None or opponent_choice is None:
            result = await _settle_wager(self.game_id)
            if not result.get("settled"):
                self._queue_recovery_if_needed(result)
                if result.get("reason") == "not_found":
                    description = (
                        "انتهت الجولة، لكن مفيش سجل رهان محفوظ. مفيش نتيجة مالية هتتحسب "
                        "من غير تأكيد قاعدة البيانات؛ راجعوا الرصيد قبل إعادة المحاولة."
                    )
                else:
                    description = (
                        "الرهان لسه مسجل بأمان. البوت هيعيد محاولة التسوية تلقائيًا "
                        "من غير صرف مكرر أو خسارة افتراضية."
                    )
            else:
                description = "حد اتأخر في الاختيار؛ رجعنا الرهان للطرفين بالكامل."
            embed = base_embed(
                title="⏰ التحدي انتهى",
                description=description,
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if player_choice == opponent_choice:
            result = await _settle_wager(self.game_id)
            if not result.get("settled"):
                self._queue_recovery_if_needed(result)
                description = "الرهان لسه محجوز بأمان. البوت هيعيد محاولة التسوية تلقائيًا، ولو حصل Restart هيتراجع الرهان عند بدء التشغيل."
            else:
                player_balance, opponent_balance = await asyncio.gather(
                    asyncio.to_thread(bank.get_balance, self.player.id),
                    asyncio.to_thread(bank.get_balance, self.opponent.id),
                )
                description = (
                    f"اختارتوا الاتنين **{MOVE_NAMES[player_choice]}**.\n"
                    f"**رصيدك:** {player_balance:,} {COIN}\n"
                    f"**رصيد الخصم:** {opponent_balance:,} {COIN}"
                )
            embed = base_embed(
                title="🤝 تعادل!",
                description=description,
                color=THEME["grey"] if result.get("settled") else THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if BEATS[player_choice] == opponent_choice:
            winner, winning_move, losing_move = self.player, player_choice, opponent_choice
        else:
            winner, winning_move, losing_move = self.opponent, opponent_choice, player_choice

        result = await _settle_wager(self.game_id, winner.id)

        if not result.get("settled"):
            self._queue_recovery_if_needed(result, winner.id)
            embed = base_embed(
                title="⚠️ الرهان محفوظ",
                description=(
                    "حصل عطل أثناء تسوية الرهان. المبلغ ما زال مسجلًا في قاعدة البنك، "
                    "ومش هنكرر الخصم أو نصرف جائزة غير مؤكدة. راجعوا حالة البوت قبل بدء رهان جديد."
                ),
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if result.get("status") == "refunded":
            embed = base_embed(
                title="⚠️ رجّعنا الرهان",
                description=(
                    "تعذر صرف الجائزة بأمان بسبب حد الرصيد؛ رجعنا رهان الطرفين بالكامل. "
                    "مفيش فائز مالي في الجولة دي."
                ),
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        winner_balance = await asyncio.to_thread(bank.get_balance, winner.id)
        embed = base_embed(
            title="🏆 فايز!",
            description=(
                f"**{MOVE_NAMES[winning_move]}** كسبت **{MOVE_NAMES[losing_move]}**.\n\n"
                f"🎉 الفائز: {winner.mention}\n"
                f"**رصيدك:** {winner_balance:,} {COIN}"
            ),
            color=THEME["gold"],
        )
        embed.set_thumbnail(url=winner.display_avatar.url)
        await self.publish_result(embed, interaction)

    async def on_timeout(self):
        await self.finish()

    async def abort(self):
        async with self._choice_lock:
            if self.done:
                return
            self.done = True
            accepted = self.accepted

        if not accepted:
            try:
                await asyncio.to_thread(bank.expire_rps_challenge, self.game_id)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not expire aborted RPS invitation game_id=%s",
                    self.game_id,
                )
            embed = base_embed(
                title="🛑 التحدي اتوقف",
                description="تم إيقاف الدعوة قبل قبولها. مفيش أي رصيد اتحجز أو اتخصم.",
                color=THEME["grey"],
            )
            return await self.publish_result(embed)

        settlement = await _settle_wager(self.game_id)
        if not settlement.get("settled"):
            self._queue_recovery_if_needed(settlement)
        description = (
            "تم إيقاف التحدي وإرجاع الرهان للطرفين."
            if settlement.get("settled")
            else (
                "التحدي اتوقف؛ الرهان لسه محفوظ وهنراجع التسوية تلقائيًا."
                if settlement.get("reason") != "not_found"
                else "تعذر العثور على سجل رهان. مفيش خصم أو صرف هنعلنه من غير تحقق."
            )
        )
        embed = base_embed(
            title="🛑 التحدي اتوقف",
            description=description,
            color=THEME["orange"],
        )
        await self.publish_result(embed)

    async def choose(self, interaction: discord.Interaction, move: str):
        # A second click may queue behind the first panel edit. Acknowledge it
        # before waiting for the lock so its Discord interaction cannot expire.
        if self._choice_lock.locked():
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except discord.HTTPException:
                logging.getLogger(__name__).warning(
                    "Could not defer queued RPS interaction game_id=%s",
                    self.game_id,
                    exc_info=True,
                )
                return

        async with self._choice_lock:
            if self.done:
                return await self._private_notice(interaction, "التحدي خلص بالفعل.")
            if not self.accepted:
                return await self._private_notice(interaction, "الخصم لازم يقبل التحدي الأول؛ لسه مفيش رهان اتحجز.")
            if interaction.user.id not in (self.player.id, self.opponent.id):
                return await self._private_notice(interaction, "مش طرف في التحدي.")
            if self.deadline is not None and time.monotonic() >= self.deadline:
                return await self._private_notice(
                    interaction,
                    "انتهى وقت الاختيار؛ الاختيارات المتأخرة مش هتغيّر نتيجة الجولة.",
                )
            if interaction.user.id in self.choices:
                return await self._private_notice(interaction, "اختارت خلاص!")

            self.choices[interaction.user.id] = move
            if len(self.choices) == 2:
                should_finish = True
                if not interaction.response.is_done():
                    try:
                        # Settlement may involve a busy SQLite lock and several
                        # retries. Acknowledge the final move before starting it.
                        await interaction.response.defer(ephemeral=True, thinking=True)
                    except (discord.HTTPException, discord.InteractionResponded):
                        logging.getLogger(__name__).warning(
                            "Could not defer final RPS move before settlement game_id=%s user_id=%s",
                            self.game_id,
                            interaction.user.id,
                            exc_info=True,
                        )
            else:
                should_finish = False
                await self._private_notice(interaction, "✅ سجلنا اختيارك بسرية.")
                self._refresh_panel()
                if self.message is not None:
                    try:
                        await self.message.edit(view=self)
                    except discord.NotFound:
                        logging.getLogger(__name__).info(
                            "RPS panel disappeared while recording a choice game_id=%s",
                            self.game_id,
                        )
                    except discord.HTTPException:
                        logging.getLogger(__name__).warning(
                            "Could not refresh RPS choice status game_id=%s",
                            self.game_id,
                            exc_info=True,
                        )

        if should_finish:
            await self.finish(interaction)

    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "🪨")

    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "📄")

    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "✂️")


# --- COG COMMANDS ---
class RPS(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._escrow_ready = False

    async def cog_load(self):
        logger = logging.getLogger(__name__)
        try:
            # Capture panel references before the initial refund mutates escrow state.
            expired_challenges = await asyncio.to_thread(bank.expire_pending_rps_challenges)
            records = await asyncio.to_thread(bank.active_rps_wager_records)
            refunded = await asyncio.to_thread(bank.refund_orphaned_rps_wagers)
            pending_wagers = await asyncio.to_thread(bank.active_rps_wager_ids)
            active_ids = set(pending_wagers)
            records_by_id = {str(record["game_id"]): record for record in records}

            for challenge in expired_challenges:
                if challenge.get("channel_id") and challenge.get("message_id"):
                    task = asyncio.create_task(
                        _publish_expired_rps_challenge_panel(self.bot, challenge)
                    )
                    _STARTUP_PANEL_TASKS.add(task)
                    task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

            for game_id, record in records_by_id.items():
                has_panel = bool(record.get("channel_id") and record.get("message_id"))
                if game_id in active_ids:
                    callback = (
                        partial(_publish_interrupted_rps_panel, self.bot, record)
                        if has_panel
                        else None
                    )
                    _schedule_wager_recovery(game_id, on_recovered=callback)
                    continue

                receipt = await asyncio.to_thread(bank.rps_wager_status, game_id)
                if (
                    has_panel
                    and receipt is not None
                    and receipt.get("status") == "refunded"
                    and receipt.get("outcome") == "refund"
                ):
                    task = asyncio.create_task(
                        _publish_interrupted_rps_panel(self.bot, record, receipt)
                    )
                    _STARTUP_PANEL_TASKS.add(task)
                    task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

            # Defensive fallback if an older/partial schema yields active IDs without rows.
            for game_id in pending_wagers:
                if str(game_id) not in records_by_id:
                    _schedule_wager_recovery(game_id)
        except Exception:
            self._escrow_ready = False
            logger.exception(
                "RPS escrow recovery could not be verified; disabling new RPS wagers until restart/reload"
            )
            return

        self._escrow_ready = True
        if refunded:
            logger.warning(
                "Refunded %s interrupted RPS wager(s) during startup", refunded
            )
        if pending_wagers:
            logger.warning(
                "Scheduled background recovery for %s unresolved RPS wager(s)",
                len(pending_wagers),
            )

    @commands.hybrid_command(
        name="حومص",
        aliases=["rps", "rock-paper-scissors", "rockpaperscissors", "حجرة", "مقص"],
        description="تحدَّ عضوًا في حجرة ورقة مقص برهان",
    )
    async def rps(self, ctx, bet: str | None = None, opponent: discord.Member | None = None):
        if not self._escrow_ready:
            return await ctx.send(
                view=embed_to_v2(
                    base_embed(
                        title="الرهان غير متاح مؤقتًا",
                        description=(
                            "سجل رهانات البنك لم يكتمل التحقق منه بعد. "
                            "مش هنبدأ رهان جديد لحد ما قاعدة البيانات تبقى آمنة."
                        ),
                        color=THEME["orange"],
                    )
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        if is_active_game(self.bot, ctx.channel.id):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة شغالة في الروم؛ استنوا لما تخلص قبل ما تبدأ تحديًا جديدًا.",
                "warning",
            )

        amount = parse_bet_amount(bet, max_bet=MAX_BET)

        usage = "/حومص" if ctx.interaction is not None else f"{ctx.prefix}حومص"
        if amount < MIN_BET:
            return await send_status(
                ctx,
                "قيمة الرهان مش مناسبة",
                f"مثال: `{usage} 100 @صاحبك`. أقل رهان **{MIN_BET:,}** {COIN}.",
                "warning",
            )
        if amount > MAX_BET:
            return await send_status(
                ctx,
                "قيمة الرهان أعلى من الحد",
                f"أقصى رهان مسموح هو **{MAX_BET:,} {COIN}**.",
                "warning",
            )
        if opponent is None:
            return await send_status(ctx, "حدد خصمك", "منشن عضوًا من السيرفر لبدء التحدي.", "warning")
        if opponent.id == ctx.author.id:
            return await send_status(ctx, "اختار لاعبًا آخر", "مينفعش تتحدى نفسك في حجرة ورقة مقص.", "warning")
        if opponent.bot:
            return await send_status(ctx, "الخصم غير صالح", "حسابات البوتات مش متاحة للتحديات.", "warning")

        currency = get_currency(self.bot)
        if currency is None:
            return await send_status(
                ctx,
                "الاقتصاد غير متاح",
                "نظام العملات مش متاح حاليًا؛ التحدي اتوقف قبل حجز أي رصيد.",
                "warning",
            )

        try:
            player_balance, opponent_balance = await asyncio.gather(
                asyncio.to_thread(bank.get_balance, ctx.author.id),
                asyncio.to_thread(bank.get_balance, opponent.id),
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not verify RPS challenge balances challenger_id=%s opponent_id=%s",
                ctx.author.id,
                opponent.id,
            )
            return await send_status(
                ctx,
                "تعذر التحقق من الرصيد",
                "حصل عطل أثناء قراءة الأرصدة. مفيش رهان اتعمل؛ جرّب تاني بعد شوية.",
                "warning",
            )

        if player_balance < amount:
            return await send_status(
                ctx,
                "رصيدك مش كفاية",
                f"رصيدك الحالي **{player_balance:,} {COIN}**، ومحتاج **{amount:,} {COIN}** عشان تبعت التحدي.",
                "warning",
            )
        if opponent_balance < amount:
            return await send_status(
                ctx,
                "رصيد الخصم مش كفاية",
                f"{opponent.mention} رصيده الحالي **{opponent_balance:,} {COIN}**، وأقل رهان مطلوب **{amount:,} {COIN}**. مفيش رصيد اتحجز.",
                "warning",
            )

        game_id = uuid.uuid4().hex
        view = RPSView(ctx, ctx.author, opponent, amount, game_id)
        task = asyncio.current_task()
        if task is None or not register_game_task(
            self.bot,
            ctx.channel.id,
            task,
            ctx.author.id,
            "Rock Paper Scissors",
            view=view,
        ):
            view.stop()
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة شغالة في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ تحديًا جديدًا.",
                "warning",
            )

        try:
            try:
                created = await asyncio.to_thread(
                    bank.create_rps_challenge,
                    game_id,
                    ctx.author.id,
                    opponent.id,
                    amount,
                    channel_id=int(ctx.channel.id),
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not create RPS challenge invitation game_id=%s",
                    game_id,
                )
                view.stop()
                await send_status(
                    ctx,
                    "تعذر إنشاء التحدي",
                    "قاعدة بيانات الرهانات لم تؤكد إنشاء الدعوة. مفيش رصيد اتحجز؛ جرّب تاني.",
                    "warning",
                )
                return

            if not created:
                view.stop()
                await send_status(
                    ctx,
                    "تعذر إنشاء التحدي",
                    "لم نقدر نحفظ الدعوة بأمان. مفيش رصيد اتحجز؛ جرّب تاني.",
                    "warning",
                )
                return

            try:
                view.message = await ctx.send(
                    view=view,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        users=[opponent],
                        roles=False,
                        replied_user=False,
                    ),
                )
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    bank.cancel_rps_challenge,
                    game_id,
                    ctx.author.id,
                )
                raise
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not publish RPS challenge invitation game_id=%s requester_id=%s",
                    game_id,
                    ctx.author.id,
                )
                try:
                    await asyncio.to_thread(
                        bank.cancel_rps_challenge,
                        game_id,
                        ctx.author.id,
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not cancel unpublished RPS invitation game_id=%s",
                        game_id,
                    )
                await send_status(
                    ctx,
                    "تعذر إرسال التحدي",
                    "حصل عطل أثناء إرسال الدعوة. الدعوة اتلغت أو هتنتهي بأمان من غير حجز أي عملات.",
                    "warning",
                )
                return

            try:
                stored_reference = await asyncio.to_thread(
                    bank.set_rps_challenge_message_reference,
                    game_id,
                    int(ctx.channel.id),
                    int(view.message.id),
                )
                if not stored_reference:
                    logging.getLogger(__name__).error(
                        "RPS invitation exists but its panel reference was not saved game_id=%s",
                        game_id,
                    )
            except Exception:
                # This reference is only for cleanup; acceptance/escrow remains guarded by DB state.
                logging.getLogger(__name__).exception(
                    "Could not persist pending RPS invitation panel reference game_id=%s",
                    game_id,
                )
            view.schedule_deadline(60.0)
            await view.wait()
        except asyncio.CancelledError:
            await view.abort()
            raise
        except Exception:
            logging.getLogger(__name__).exception(
                "Unexpected RPS command lifecycle error game_id=%s requester_id=%s",
                game_id,
                ctx.author.id,
            )
            if not view.done:
                await view.abort()
            raise
        finally:
            release_game_task(self.bot, ctx.channel.id, task)


async def setup(bot):
    await bot.add_cog(RPS(bot))
