"""Solo coinflip betting game with durable economy settlement and Components V2 UI."""

import asyncio
import logging
import re
import secrets
import uuid
from functools import partial

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

from cogs.casino import bank
from cogs.utility.theme import THEME, StatusPanel

logger = logging.getLogger(__name__)

MIN_BET = 10
MAX_BET = 5_000
GROSS_WIN_MULTIPLIER_NUMERATOR = 190
GROSS_WIN_MULTIPLIER_DENOMINATOR = 100
COIN = "<:ckrcoin:1513743032124575784>"
SIDES = {
    "heads": {"label": "صورة", "emoji": "🪙"},
    "tails": {"label": "كتابة", "emoji": "🪙"},
}
_RECOVERY_DELAYS_SECONDS = (2, 5, 15, 30, 60)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_SESSIONS: dict[int, "CoinflipView"] = {}


def _gross_payout(stake: int) -> int:
    """Return total win payout including the original stake, with a 5% house edge."""
    return (stake * GROSS_WIN_MULTIPLIER_NUMERATOR) // GROSS_WIN_MULTIPLIER_DENOMINATOR


class CoinflipView(LayoutView):
    """One-message solo game. Only its requester can wager; no money moves before choice."""

    def __init__(self, cog, requester, amount: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester = requester
        self.amount = amount
        self.message = None
        self.game_id = None
        self.done = False
        self.resolving = False
        self._panel_text = TextDisplay(self._waiting_content())
        self._buttons = {}

        for side in ("heads", "tails"):
            button = discord.ui.Button(
                label=SIDES[side]["label"],
                emoji=SIDES[side]["emoji"],
                style=discord.ButtonStyle.secondary,
                custom_id=f"coinflip_{side}",
            )
            button.callback = partial(self.choose, side=side)
            self._buttons[side] = button

        cancel = discord.ui.Button(
            label="إلغاء",
            emoji="✕",
            style=discord.ButtonStyle.secondary,
            custom_id="coinflip_cancel",
        )
        cancel.callback = self.cancel
        self._buttons["cancel"] = cancel
        self.add_item(
            Container(
                self._panel_text,
                Separator(),
                ActionRow(*self._buttons.values()),
                accent_color=THEME["teal"],
            )
        )

    def _waiting_content(self) -> str:
        payout = _gross_payout(self.amount)
        return (
            "# رمي العملة\n"
            f"**الرهان:** {self.amount:,} {COIN}\n"
            f"**إجمالي الصرف عند الفوز:** {payout:,} {COIN} (1.9x شامل الرهان)\n"
            f"**صافي الربح عند الفوز:** {payout - self.amount:,} {COIN}\n\n"
            "اختار صورة أو كتابة. رصيدك مش هيتخصم غير لما تختار، "
            "والنتيجة هتظهر في الرسالة دي من غير رسائل إضافية."
        )

    def _disable_buttons(self) -> None:
        for button in self._buttons.values():
            button.disabled = True

    async def _edit_message(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            logger.info("Coinflip result message was deleted game_id=%s", self.game_id)
        except discord.HTTPException:
            logger.warning("Could not update coinflip message game_id=%s", self.game_id, exc_info=True)

    def _release_session(self) -> None:
        if _ACTIVE_SESSIONS.get(self.requester.id) is self:
            _ACTIVE_SESSIONS.pop(self.requester.id, None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "القائمة دي مش بتاعتك",
                    "كل لاعب يقدر يختار في جلسة الرهان اللي بدأها هو بس.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def choose(self, interaction: discord.Interaction, side: str) -> None:
        if self.done or self.resolving:
            return await interaction.response.send_message(
                view=StatusPanel("الجولة اتقفلت", "تم تسجيل الاختيار أو انتهاء الجلسة بالفعل.", "warning"),
                ephemeral=True,
            )
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                view=StatusPanel("الاختيار مش متاح", "الرهان ده خاص بالشخص اللي بدأه.", "warning"),
                ephemeral=True,
            )

        self.resolving = True
        self.message = interaction.message or self.message
        self._disable_buttons()
        self._panel_text.content = (
            "# جاري تأكيد الرهان\n"
            "قفلنا الاختيار وبنحفظ نتيجة الجولة والخصم والتسوية بشكل آمن. استنى لحظة."
        )
        await interaction.response.edit_message(view=self)

        game_id = uuid.uuid4().hex
        result_side = secrets.choice(tuple(SIDES))
        won = side == result_side
        payout = _gross_payout(self.amount) if won else 0
        self.game_id = game_id

        try:
            created = await asyncio.to_thread(
                bank.create_casino_wager,
                game_id,
                self.requester.id,
                "coinflip",
                self.amount,
                side,
                result_side,
                won,
                payout,
            )
        except asyncio.CancelledError:
            self.cog._schedule_recovery(game_id, force_refund=True, callback=self._recovered)
            raise
        except Exception:
            logger.exception("Could not confirm coinflip stake game_id=%s", game_id)
            self._panel_text.content = (
                "# جاري التحقق من الرهان\n"
                "حصل تأخير في قاعدة البيانات. حفظنا معرّف العملية، والبوت هيتأكد من حالتها "
                "من غير ما يكرر الخصم أو الصرف."
            )
            await self._edit_message()
            self.cog._schedule_recovery(game_id, force_refund=True, callback=self._recovered)
            return

        if not created.get("created"):
            reason = created.get("reason")
            if reason == "insufficient_funds":
                balance = int(created.get("balance", 0))
                message = f"رصيدك الحالي **{balance:,}** {COIN}، ومحتاج **{self.amount:,}**."
            elif reason == "duplicate_game":
                message = "معرّف الجولة مستخدم بالفعل؛ لم نسحب أي مبلغ لجولة جديدة."
            else:
                message = "تعذر قبول الرهان بأمان؛ لم يتم تسجيل خصم جديد."
            return await self._finish_without_bet("# الرهان مرفوض\n" + message)

        try:
            settled = await self.cog._settle_with_retries(game_id)
        except asyncio.CancelledError:
            self.cog._schedule_recovery(game_id, force_refund=True, callback=self._recovered)
            raise
        if not settled.get("settled"):
            self._panel_text.content = (
                "# النتيجة محفوظة — التسوية قيد المعالجة\n"
                "سجلنا الرهان والنتيجة، لكن تحديث الرصيد اتأخر. العملية محفوظة، "
                "والبوت هيعيد المحاولة تلقائيًا من غير أي صرف مكرر."
            )
            await self._edit_message()
            self.cog._schedule_recovery(game_id, force_refund=False, callback=self._recovered)
            return

        await self._finalize(settled)

    async def _finish_without_bet(self, content: str) -> None:
        self.done = True
        self.resolving = False
        self._panel_text.content = content
        self._disable_buttons()
        await self._edit_message()
        self._release_session()
        self.stop()

    async def _recovered(self, result: dict) -> None:
        if result.get("settled"):
            await self._finalize(result)
        elif result.get("reason") == "not_found":
            await self._finish_without_bet(
                "# لم يتأكد تسجيل الرهان\n"
                "لم نلاقِ عملية محفوظة للمعرّف ده؛ مفيش نتيجة هنعلنها من غير تسوية مؤكدة. "
                "راجع رصيدك قبل ما تبدأ محاولة جديدة."
            )

    async def _finalize(self, result: dict) -> None:
        self.done = True
        self.resolving = False
        self._disable_buttons()
        side = str(result.get("choice", ""))
        result_side = str(result.get("result", ""))
        side_label = SIDES.get(side, {}).get("label", side)
        result_label = SIDES.get(result_side, {}).get("label", result_side)
        stake = int(result.get("stake", self.amount))
        payout = int(result.get("payout", 0))
        balance_after = result.get("balance_after")

        if result.get("status") == "refunded":
            if result.get("outcome") == "balance_limit":
                self._panel_text.content = (
                    "# تم إرجاع الرهان\n"
                    "تعذر إضافة المكسب من غير تجاوز الحد الآمن للرصيد، فتم إرجاع قيمة الرهان بالكامل."
                )
            else:
                self._panel_text.content = (
                    "# تم إرجاع الرهان\n"
                    "الجولة اتوقفت قبل اكتمالها، فرجعنا مبلغ الرهان بدل ما نعتبره خسارة."
                )
        elif result.get("outcome") == "win":
            balance_line = f"\n**رصيدك بعد التسوية:** {int(balance_after):,} {COIN}" if balance_after is not None else ""
            self._panel_text.content = (
                "# كسبت الرهان\n"
                f"اختيارك: **{side_label}** · نتيجة العملة: **{result_label}**\n\n"
                f"**الرهان:** −{stake:,} {COIN}\n"
                f"**إجمالي الصرف:** +{payout:,} {COIN}\n"
                f"**صافي الربح:** +{payout - stake:,} {COIN}"
                f"{balance_line}"
            )
        else:
            balance_line = f"\n**رصيدك بعد التسوية:** {int(balance_after):,} {COIN}" if balance_after is not None else ""
            self._panel_text.content = (
                "# خسرت الرهان\n"
                f"اختيارك: **{side_label}** · نتيجة العملة: **{result_label}**\n\n"
                f"**الرهان:** −{stake:,} {COIN}\n"
                "**إجمالي الصرف:** 0\n"
                f"**صافي النتيجة:** −{stake:,} {COIN}"
                f"{balance_line}"
            )

        await self._edit_message()
        self._release_session()
        self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        if self.done or self.resolving:
            return await interaction.response.send_message(
                view=StatusPanel("الجولة اتقفلت", "مينفعش تلغي بعد بدء تسوية الرهان.", "warning"),
                ephemeral=True,
            )
        self.message = interaction.message or self.message
        self.done = True
        self._disable_buttons()
        self._panel_text.content = (
            "# تم إلغاء رمي العملة\n"
            "اتلغت الجلسة قبل اختيارك؛ مفيش أي رهان اتخصم."
        )
        await interaction.response.edit_message(view=self)
        self._release_session()
        self.stop()

    async def on_timeout(self) -> None:
        if self.done or self.resolving:
            return
        self.done = True
        self._disable_buttons()
        self._panel_text.content = (
            "# انتهت صلاحية الجلسة\n"
            "الوقت خلص قبل ما تختار؛ مفيش أي رهان اتخصم."
        )
        await self._edit_message()
        self._release_session()


class Coinflip(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._wagers_ready = False

    async def cog_load(self):
        try:
            await asyncio.to_thread(bank.refund_orphaned_casino_wagers)
            active_ids = await asyncio.to_thread(bank.active_casino_wager_ids)
        except Exception:
            self._wagers_ready = False
            logger.exception(
                "Solo casino wager recovery could not be verified; disabling new coinflip wagers"
            )
            return

        self._wagers_ready = True
        for game_id in active_ids:
            self._schedule_recovery(game_id, force_refund=True)
        if active_ids:
            logger.warning("Scheduled refunds for %s unfinished solo casino wager(s)", len(active_ids))

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

    @commands.hybrid_command(
        name="coinflip",
        aliases=["flip", "عملة", "رمي_العملة"],
        description="راهن بعملة افتراضية على نتيجة رمي العملة",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def coinflip(self, ctx, bet: str):
        if not self._wagers_ready:
            return await ctx.send(
                view=StatusPanel(
                    "الرهان غير متاح مؤقتًا",
                    "قاعدة بيانات الرهانات بتتراجع دلوقتي. مفيش أي رهان جديد هيتقبل لحد ما نتأكد إن التسويات آمنة.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        raw_bet = bet.strip().replace(",", "") if isinstance(bet, str) else ""
        amount = int(raw_bet) if re.fullmatch(r"[0-9]+", raw_bet) else 0
        if not MIN_BET <= amount <= MAX_BET:
            usage = "/coinflip bet:100" if ctx.interaction is not None else f"{ctx.prefix}coinflip 100"
            return await ctx.send(
                view=StatusPanel(
                    "قيمة الرهان مش مناسبة",
                    f"استخدم {usage}. أقل رهان **{MIN_BET:,}** وأقصى رهان **{MAX_BET:,}** {COIN}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        current = _ACTIVE_SESSIONS.get(ctx.author.id)
        if current is not None and not current.done and not current.is_finished():
            return await ctx.send(
                view=StatusPanel(
                    "عندك جلسة مفتوحة",
                    "كمّل أو ألغِ جلسة رمي العملة الحالية قبل ما تبدأ جلسة جديدة.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        view = CoinflipView(self, ctx.author, amount)
        _ACTIVE_SESSIONS[ctx.author.id] = view
        try:
            view.message = await ctx.send(view=view)
        except asyncio.CancelledError:
            view._release_session()
            raise
        except Exception:
            view._release_session()
            logger.exception("Could not create coinflip view requester_id=%s", ctx.author.id)
            raise


async def setup(bot):
    await bot.add_cog(Coinflip(bot))
