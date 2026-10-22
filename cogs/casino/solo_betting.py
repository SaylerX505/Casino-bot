"""Shared solo betting games with durable economy settlement and Components V2 UI."""

import asyncio
import logging
import re
import secrets
import uuid
from collections.abc import Callable
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
DICE_CHOICES = {str(number): {"label": str(number), "emoji": None} for number in range(1, 7)}
SLOT_SYMBOLS = ("🍒", "🍋", "🍊", "🍇", "⭐", "💎")
SLOT_TRIPLE_PAYOUTS = {"🍒": 500, "🍋": 600, "🍊": 700, "🍇": 800, "⭐": 1_000, "💎": 1_500}
SLOT_CHOICES = {"spin": {"label": "تدوير", "emoji": "🎰"}}
_RECOVERY_DELAYS_SECONDS = (2, 5, 15, 30, 60)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_SESSIONS: dict[int, "SoloWagerView"] = {}


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


class SoloWagerView(LayoutView):
    """One-message solo game with requester-bound controls and durable settlement."""

    def __init__(
        self,
        cog,
        requester,
        amount: int,
        *,
        game_type: str = "coinflip",
        game_title: str = "رمي العملة",
        choices: dict | None = None,
        payout_numerator: int = GROSS_WIN_MULTIPLIER_NUMERATOR,
        payout_denominator: int = GROSS_WIN_MULTIPLIER_DENOMINATOR,
        odds_description: str = "1 من 2",
        instructions: str = "اختار صورة أو كتابة.",
        payout_description: str | None = None,
        outcome_resolver: Callable[[str, int], tuple[str, bool, int]] | None = None,
    ):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester = requester
        self.amount = amount
        self.game_type = game_type
        self.game_title = game_title
        self.choices = choices or SIDES
        self.payout_numerator = payout_numerator
        self.payout_denominator = payout_denominator
        self.odds_description = odds_description
        self.instructions = instructions
        self.payout_description = payout_description
        self.outcome_resolver = outcome_resolver
        self.message = None
        self.game_id = None
        self.done = False
        self.resolving = False
        self._panel_text = TextDisplay(self._waiting_content())
        self._buttons = {}

        choice_buttons = []
        for choice, metadata in self.choices.items():
            button = discord.ui.Button(
                label=metadata["label"],
                emoji=metadata.get("emoji"),
                style=discord.ButtonStyle.secondary,
                custom_id=f"{self.game_type}_{choice}",
            )
            button.callback = partial(self.choose, side=choice)
            self._buttons[choice] = button
            choice_buttons.append(button)

        cancel = discord.ui.Button(
            label="إلغاء",
            emoji="✕",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_cancel",
        )
        cancel.callback = self.cancel
        self._buttons["cancel"] = cancel

        elements = [self._panel_text, Separator()]
        if len(choice_buttons) <= 2:
            elements.append(ActionRow(*choice_buttons, cancel))
        else:
            split = (len(choice_buttons) + 1) // 2
            elements.append(ActionRow(*choice_buttons[:split]))
            elements.append(ActionRow(*choice_buttons[split:], cancel))
        self.add_item(Container(*elements, accent_color=THEME["teal"]))

    def _waiting_content(self) -> str:
        payout = _gross_payout(
            self.amount, self.payout_numerator, self.payout_denominator
        )
        multiplier = self.payout_numerator / self.payout_denominator
        payout_lines = (
            f"**جدول الجوائز:** {self.payout_description}\n"
            if self.payout_description
            else (
                f"**إجمالي الصرف عند الفوز:** {payout:,} {COIN} "
                f"({multiplier:.1f}x شامل الرهان)\n"
                f"**صافي الربح عند الفوز:** {payout - self.amount:,} {COIN}\n"
            )
        )
        return (
            f"# {self.game_title}\n"
            f"**الرهان:** {self.amount:,} {COIN}\n"
            f"**فرصة الفوز:** {self.odds_description}\n"
            f"{payout_lines}\n"
            f"{self.instructions} رصيدك مش هيتخصم غير لما تختار، "
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
            f"# جاري تأكيد {self.game_title}\n"
            "قفلنا الاختيار وبنحفظ نتيجة الجولة والخصم والتسوية بشكل آمن. استنى لحظة."
        )
        await interaction.response.edit_message(view=self)

        game_id = uuid.uuid4().hex
        try:
            if self.outcome_resolver is None:
                result_side = secrets.choice(tuple(self.choices))
                won = side == result_side
                payout = _gross_payout(
                    self.amount, self.payout_numerator, self.payout_denominator
                ) if won else 0
            else:
                result_side, won, payout = self.outcome_resolver(side, self.amount)
        except Exception:
            logger.exception("Could not calculate solo wager outcome game_type=%s", self.game_type)
            await self._finish_without_bet(
                "# تعذر حساب النتيجة\n"
                "مفيش رهان اتخصم. جرّب تبدأ جولة جديدة بعد مراجعة الحالة."
            )
            return
        self.game_id = game_id

        try:
            created = await asyncio.to_thread(
                bank.create_casino_wager,
                game_id,
                self.requester.id,
                self.game_type,
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
            logger.exception("Could not confirm solo wager game_id=%s", game_id)
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
        choice = str(result.get("choice", ""))
        result_side = str(result.get("result", ""))
        choice_label = self.choices.get(choice, {}).get("label", choice)
        result_label = self.choices.get(result_side, {}).get("label", result_side)
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
                f"# كسبت الرهان · {self.game_title}\n"
                f"اختيارك: **{choice_label}** · النتيجة: **{result_label}**\n\n"
                f"**الرهان:** −{stake:,} {COIN}\n"
                f"**إجمالي الصرف:** +{payout:,} {COIN}\n"
                f"**صافي الربح:** +{payout - stake:,} {COIN}"
                f"{balance_line}"
            )
        else:
            balance_line = f"\n**رصيدك بعد التسوية:** {int(balance_after):,} {COIN}" if balance_after is not None else ""
            self._panel_text.content = (
                f"# خسرت الرهان · {self.game_title}\n"
                f"اختيارك: **{choice_label}** · النتيجة: **{result_label}**\n\n"
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
            f"# تم إلغاء {self.game_title}\n"
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
            f"# انتهت صلاحية {self.game_title}\n"
            "الوقت خلص قبل ما تختار؛ مفيش أي رهان اتخصم."
        )
        await self._edit_message()
        self._release_session()


class SoloBetting(commands.Cog):
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
                "Solo casino wager recovery could not be verified; disabling new wagers"
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
                    "الرهان غير متاح مؤقتًا",
                    "قاعدة بيانات الرهانات بتتراجع دلوقتي. مفيش أي رهان جديد هيتقبل لحد ما نتأكد إن التسويات آمنة.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        raw_bet = bet.strip().replace(",", "") if isinstance(bet, str) else ""
        amount = int(raw_bet) if re.fullmatch(r"[0-9]+", raw_bet) else 0
        if not MIN_BET <= amount <= MAX_BET:
            usage = f"/{command_name} bet:100" if ctx.interaction is not None else f"{ctx.prefix}{command_name} 100"
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
                    "كمّل أو ألغِ جلسة الرهان الحالية قبل ما تبدأ جلسة جديدة.",
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
        )
        _ACTIVE_SESSIONS[ctx.author.id] = view
        try:
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
        aliases=["flip", "عملة", "رمي_العملة"],
        description="راهن بعملة افتراضية على نتيجة رمي العملة",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def coinflip(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="coinflip",
            game_title="رمي العملة",
            choices=SIDES,
            payout_numerator=190,
            odds_description="1 من 2",
            instructions="اختار صورة أو كتابة.",
            command_name="coinflip",
        )

    @commands.hybrid_command(
        name="dice",
        aliases=["نرد", "زهر"],
        description="راهن بعملة افتراضية على رقم النرد",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def dice(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="dice",
            game_title="رمي النرد",
            choices=DICE_CHOICES,
            payout_numerator=570,
            odds_description="1 من 6",
            instructions="اختار رقم من 1 إلى 6. الرقم الصحيح يكسبك 5.7x من قيمة الرهان.",
            command_name="dice",
        )

    @commands.hybrid_command(
        name="slots",
        aliases=["slot", "سلوت", "ماكينة"],
        description="لف ماكينة الحظ مقابل رهان بعملة افتراضية",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def slots(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="slots",
            game_title="ماكينة الحظ",
            choices=SLOT_CHOICES,
            payout_numerator=170,
            odds_description="الرموز مستقلة ومتساوية الاحتمال",
            instructions="اضغط تدوير لتجربة 3 بكرات.",
            command_name="slots",
            payout_description=(
                "زوج متطابق: 90/216 احتمال (41.67%)، إجمالي الصرف 1.7x. "
                "أي ثلاثية: 6/216 (2.78%)؛ كل رمز محدد: 1/216 (0.46%). "
                "صافي الجوائز الثلاثية: 5x (🍒)، 6x (🍋)، 7x (🍊)، "
                "8x (🍇)، 10x (⭐)، 15x (💎) إجمالي الصرف شامل الرهان."
            ),
            outcome_resolver=_spin_slots,
        )


async def setup(bot):
    await bot.add_cog(SoloBetting(bot))
