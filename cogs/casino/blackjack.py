"""Single-player blackjack backed by the durable virtual-currency wager ledger."""

import asyncio
import logging
import re
import secrets
import uuid

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

from cogs.casino import bank
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.casino.solo_betting import (
    COIN,
    MAX_BET,
    MIN_BET,
    claim_solo_session,
    recover_orphaned_casino_wagers,
    release_solo_session,
)
from cogs.utility.theme import THEME, StatusPanel

logger = logging.getLogger(__name__)

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
DEALER_STANDS_AT = 17
NATURAL_PAYOUT = 250
REGULAR_PAYOUT = 200
PAYOUT_DENOMINATOR = 100
RECOVERY_DELAYS = (2, 5, 15, 30, 60)


def _new_shuffled_deck():
    deck = [(rank, suit) for suit in SUITS for rank in RANKS]
    secrets.SystemRandom().shuffle(deck)
    return deck


def _hand_value(cards):
    total = 0
    aces = 0
    for rank, _suit in cards:
        if rank == "A":
            total += 11
            aces += 1
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _is_natural_blackjack(cards):
    return len(cards) == 2 and _hand_value(cards) == 21


def _initial_outcome(player_cards, dealer_cards):
    player_natural = _is_natural_blackjack(player_cards)
    dealer_natural = _is_natural_blackjack(dealer_cards)
    if player_natural and dealer_natural:
        return "push"
    if player_natural:
        return "natural"
    if dealer_natural:
        return "loss"
    return None


def _final_outcome(player_cards, dealer_cards):
    player = _hand_value(player_cards)
    dealer = _hand_value(dealer_cards)
    if player > 21:
        return "loss"
    if dealer > 21:
        return "win"
    if player > dealer:
        return "win"
    if player < dealer:
        return "loss"
    return "push"


def _cards_text(cards):
    return "  ".join(f"[{rank}{suit}]" for rank, suit in cards) or "—"


class BlackjackView(DiagnosticViewMixin, LayoutView):
    """A one-message hand controlled only by its original player."""

    def __init__(self, cog, requester, amount, *, deck=None):
        super().__init__(timeout=180)
        self.cog = cog
        self.requester = requester
        self.amount = amount
        self.game_id = uuid.uuid4().hex
        self.deck = list(deck) if deck is not None else _new_shuffled_deck()
        self.player_cards = []
        self.dealer_cards = []
        self.message = None
        self.phase = "waiting"
        self.done = False
        self.resolving = False
        self.result_kind = None
        self.receipt = None
        self.notice = None
        self._buttons = {}
        self._render_view()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "الجولة دي مش بتاعتك",
                    "كل لاعب يقدر يتحكم في جولة Blackjack اللي بدأها بنفسه.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        return True

    def _button(self, key, label, callback, emoji=None):
        button = discord.ui.Button(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"blackjack_{key}",
        )
        button.callback = callback
        self._buttons[key] = button
        return button

    def _render_view(self):
        self.clear_items()
        self._buttons.clear()
        elements = [TextDisplay(self._content()), Separator()]
        if self.phase == "waiting":
            elements.append(ActionRow(
                self._button("deal", "ابدأ اللعب", self.start_game, "🃏"),
                self._button("cancel", "إلغاء", self.cancel, "✕"),
            ))
        elif self.phase == "playing":
            elements.append(ActionRow(
                self._button("hit", "اسحب بطاقة", self.hit, "🂠"),
                self._button("stand", "قف", self.stand, "⏹"),
            ))
        self.add_item(Container(*elements, accent_color=THEME["gold"]))

    async def _recover_after_component_error(self, incident_id: str) -> None:
        """Refund an unfinished hand following an unexpected UI callback exception."""
        if self.phase == "waiting" and not self.resolving:
            await self._finish_without_bet(
                f"الجولة اتقفلت بأمان. رقم المتابعة: {incident_id}. مفيش رهان اتخصم."
            )
            return

        self.resolving = True
        self.phase = "pending"
        self.notice = (
            f"حصل خطأ أثناء الجولة (رقم المتابعة {incident_id}). "
            "بنراجع حالة الرهان؛ أي جولة غير مكتملة هيترد رهانها."
        )
        self.cog._schedule_recovery(
            self.game_id,
            force_refund=True,
            callback=self._recovered,
        )
        await self._edit()

    def _content(self):
        if self.phase == "waiting":
            return (
                "# Blackjack\n"
                f"**الرهان:** {self.amount:,} {COIN}\n"
                "**إجمالي الصرف:** 2.0x للفوز العادي، و2.5x للـ Blackjack الطبيعي. "
                "التعادل يرجّع الرهان بالكامل.\n"
                "**القواعد:** قرّب مجموعك من 21 من غير ما تتجاوزها. الموزّع يقف عند 17، "
                "والـ Blackjack الطبيعي هو 21 من أول بطاقتين.\n\n"
                "اضغط «ابدأ اللعب» لتثبيت الرهان وتوزيع الورق. مفيش خصم قبل البداية."
            )
        if self.phase == "starting":
            return f"# Blackjack\n**الرهان:** {self.amount:,} {COIN}\nجاري تثبيت الرهان وتوزيع البطاقات بأمان..."
        if self.phase == "playing":
            return (
                "# Blackjack\n"
                f"**الرهان:** {self.amount:,} {COIN}\n"
                f"**الموزّع:** {_cards_text(self.dealer_cards[:1])}  [🂠]\n"
                f"**يدك:** {_cards_text(self.player_cards)}\n"
                f"**مجموعك:** {_hand_value(self.player_cards)}\n\n"
                "اسحب بطاقة أو قف. الموزّع هيكشف ورقه ويسحب حتى يوصل إلى 17."
            )
        if self.phase in ("settling", "pending"):
            label = "جاري تسجيل النتيجة" if self.phase == "settling" else "التسوية قيد المعالجة"
            return (
                f"# Blackjack · {label}\n"
                f"**يدك:** {_cards_text(self.player_cards)} ({_hand_value(self.player_cards)})\n"
                f"**يد الموزّع:** {_cards_text(self.dealer_cards)} ({_hand_value(self.dealer_cards)})\n\n"
                f"{self.notice or 'بنحفظ النتيجة من غير ما نكرر صرف المكسب.'}"
            )
        return self._finished_content()

    def _finished_content(self):
        receipt = self.receipt
        if receipt is None:
            return f"# Blackjack\n{self.notice or 'انتهت الجلسة.'}\n\nمفيش رهان اتخصم."
        status = receipt.get("status")
        outcome = receipt.get("outcome")
        if status == "refunded":
            heading, detail = "تم رد الرهان", "الجولة اتقفلت من غير خسارة رصيد؛ الرهان رجع لمحفظتك."
        elif outcome == "push":
            heading, detail = "تعادل", "النتيجة متعادلة، فرجع لك الرهان كاملًا."
        elif outcome == "win" and self.result_kind == "natural":
            heading, detail = "Blackjack طبيعي!", "21 من أول بطاقتين؛ اتصرف العائد الخاص بيها."
        elif outcome == "win":
            heading, detail = "كسبت الجولة", "مجموعك أعلى من الموزّع أو الموزّع تجاوز 21."
        else:
            heading, detail = "انتهت الجولة", "الموزّع كسب الجولة أو مجموعك تجاوز 21."

        stake = int(receipt.get("stake", self.amount))
        payout = int(receipt.get("payout", 0))
        if status == "refunded" or outcome == "push":
            net = "**صافي النتيجة:** 0"
        elif outcome == "win":
            net = f"**صافي الربح:** +{payout - stake:,} {COIN}"
        else:
            net = f"**صافي النتيجة:** −{stake:,} {COIN}"
        balance = receipt.get("balance_after")
        balance_line = f"\n**الرصيد بعد التسوية:** {int(balance):,} {COIN}" if balance is not None else ""
        return (
            f"# Blackjack · {heading}\n{detail}\n\n"
            f"**يدك:** {_cards_text(self.player_cards)} ({_hand_value(self.player_cards)})\n"
            f"**يد الموزّع:** {_cards_text(self.dealer_cards)} ({_hand_value(self.dealer_cards)})\n\n"
            f"**الرهان:** {stake:,} {COIN}\n"
            f"**إجمالي الصرف:** {payout:,} {COIN}\n{net}{balance_line}"
        )

    async def _edit(self):
        if self.message is None:
            return
        self._render_view()
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            logger.info("Blackjack message missing game_id=%s", self.game_id)
        except discord.HTTPException:
            logger.warning("Could not edit blackjack message game_id=%s", self.game_id, exc_info=True)

    def _draw(self):
        if not self.deck:
            self.deck = _new_shuffled_deck()
        return self.deck.pop()

    async def _closed(self, interaction):
        await interaction.response.send_message(
            view=StatusPanel("الجولة اتقفلت", "ابدأ جولة جديدة بأمر Blackjack.", "warning"),
            ephemeral=True,
        )

    async def start_game(self, interaction):
        if self.phase != "waiting" or self.resolving:
            return await self._closed(interaction)
        self.resolving = True
        self.phase = "starting"
        self.message = interaction.message or self.message
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.resolving = False
            self.phase = "waiting"
            self._render_view()
            return
        await self._edit()
        try:
            wager_args = (
                self.game_id,
                self.requester.id,
                "blackjack",
                self.amount,
                "blackjack",
                "in_progress",
                False,
                0,
            )
            channel_id = getattr(interaction, "channel_id", None)
            message_id = getattr(self.message, "id", None)
            if isinstance(channel_id, int) and isinstance(message_id, int):
                created = await asyncio.to_thread(
                    bank.create_casino_wager,
                    *wager_args,
                    channel_id=channel_id,
                    message_id=message_id,
                )
            else:
                created = await asyncio.to_thread(
                    bank.create_casino_wager,
                    *wager_args,
                )
        except asyncio.CancelledError:
            self.cog._schedule_recovery(self.game_id, force_refund=True, callback=self._recovered)
            raise
        except Exception:
            logger.exception("Could not confirm blackjack wager game_id=%s", self.game_id)
            self.phase = "pending"
            self.notice = "بنراجع حالة الرهان؛ مش هنكرر الخصم أو نعلن نتيجة قبل تأكيدها."
            await self._edit()
            self.cog._schedule_recovery(self.game_id, force_refund=True, callback=self._recovered)
            return
        if not created.get("created"):
            if created.get("reason") == "insufficient_funds":
                notice = f"رصيدك {int(created.get('balance', 0)):,} {COIN}، ومحتاج {self.amount:,}."
            else:
                notice = "تعذر قبول الرهان بأمان؛ مفيش خصم جديد اتسجل."
            return await self._finish_without_bet(notice)

        self.player_cards = [self._draw(), self._draw()]
        self.dealer_cards = [self._draw(), self._draw()]
        outcome = _initial_outcome(self.player_cards, self.dealer_cards)
        if outcome is not None:
            return await self._settle_outcome(outcome)
        self.phase = "playing"
        self.resolving = False
        await self._edit()

    async def hit(self, interaction):
        if self.phase != "playing" or self.resolving:
            return await self._closed(interaction)
        self.resolving = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.resolving = False
            return
        self.player_cards.append(self._draw())
        if _hand_value(self.player_cards) > 21:
            return await self._settle_outcome("loss")
        self.resolving = False
        await self._edit()

    async def stand(self, interaction):
        if self.phase != "playing" or self.resolving:
            return await self._closed(interaction)
        self.resolving = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.resolving = False
            return
        while _hand_value(self.dealer_cards) < DEALER_STANDS_AT:
            self.dealer_cards.append(self._draw())
        await self._settle_outcome(_final_outcome(self.player_cards, self.dealer_cards))

    async def _settle_outcome(self, kind):
        self.resolving = True
        self.phase = "settling"
        self.result_kind = kind
        self.notice = "جاري تسجيل الرصيد والنتيجة في معاملة واحدة..."
        await self._edit()
        won = kind in ("natural", "win")
        payout = (
            self.amount * NATURAL_PAYOUT // PAYOUT_DENOMINATOR if kind == "natural"
            else self.amount * REGULAR_PAYOUT // PAYOUT_DENOMINATOR if kind == "win"
            else 0
        )
        final_outcome = "win" if won else "push" if kind == "push" else "loss"
        settlement = {
            "final_result": self._durable_result(kind),
            "final_won": won,
            "final_payout": payout,
            "final_outcome": final_outcome,
        }
        try:
            result = await self.cog._settle_with_retries(self.game_id, **settlement)
        except asyncio.CancelledError:
            self.cog._schedule_recovery(self.game_id, force_refund=True, callback=self._recovered)
            raise
        if not result.get("settled"):
            self.phase = "pending"
            self.notice = "النتيجة اتحددت، لكن قاعدة البيانات بتتأخر. هنكرر التسوية بأمان من غير صرف مكرر."
            await self._edit()
            self.cog._schedule_recovery(
                self.game_id, force_refund=False, settlement=settlement, callback=self._recovered
            )
            return
        await self._apply_settlement(result)

    def _durable_result(self, kind):
        player = ",".join(f"{rank}{suit}" for rank, suit in self.player_cards)
        dealer = ",".join(f"{rank}{suit}" for rank, suit in self.dealer_cards)
        return f"player={_hand_value(self.player_cards)};dealer={_hand_value(self.dealer_cards)};player_cards={player};dealer_cards={dealer};result={kind}"

    async def _apply_settlement(self, result):
        self.receipt = result
        self.phase = "finished"
        self.resolving = False
        self.done = True
        self.notice = None
        await self._edit()
        release_solo_session(self.requester.id, self)
        self.stop()

    async def _recovered(self, result):
        if result.get("settled"):
            await self._apply_settlement(result)
        elif result.get("reason") == "not_found":
            await self._finish_without_bet("تعذر تأكيد تسجيل الرهان؛ مفيش نتيجة هنتحسب ومفيش مبلغ اتخصم.")

    async def _finish_without_bet(self, notice):
        self.notice = notice
        self.phase = "finished"
        self.done = True
        self.resolving = False
        await self._edit()
        release_solo_session(self.requester.id, self)
        self.stop()

    async def cancel(self, interaction):
        if self.phase != "waiting" or self.resolving:
            return await self._closed(interaction)
        self.done = True
        self.phase = "finished"
        self.notice = "اتلغت الجلسة قبل توزيع الورق؛ مفيش رهان اتخصم."
        self._render_view()
        await interaction.response.edit_message(view=self)
        release_solo_session(self.requester.id, self)
        self.stop()

    async def on_timeout(self):
        if self.done or self.resolving:
            return
        if self.phase == "waiting":
            await self._finish_without_bet("انتهى وقت بدء الجولة؛ مفيش أي رهان اتخصم.")
            return
        if self.phase != "playing":
            return
        self.resolving = True
        self.phase = "pending"
        self.notice = "انتهى وقت التفاعل، وبنرجّع الرهان بدل ما نعتبر الجولة غير المكتملة خسارة."
        await self._edit()
        try:
            result = await self.cog._settle_with_retries(self.game_id, force_refund=True)
        except asyncio.CancelledError:
            self.cog._schedule_recovery(self.game_id, force_refund=True, callback=self._recovered)
            raise
        if result.get("settled"):
            await self._apply_settlement(result)
        else:
            await self._edit()
            self.cog._schedule_recovery(self.game_id, force_refund=True, callback=self._recovered)


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
        aliases=["bj", "بلجاك", "بلاكجاك"],
        description="العب Blackjack برهان من العملة الافتراضية",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def blackjack(self, ctx, bet: str):
        if not self._wagers_ready:
            return await ctx.send(
                view=StatusPanel(
                    "Blackjack غير متاح مؤقتًا",
                    "قاعدة بيانات الرهانات بتتراجع؛ مش هنقبل رهان جديد لحد ما نتأكد من التسويات.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        raw = bet.strip().replace(",", "") if isinstance(bet, str) else ""
        amount = int(raw) if re.fullmatch(r"[0-9]+", raw) else 0
        if not MIN_BET <= amount <= MAX_BET:
            usage = "/blackjack bet:100" if ctx.interaction is not None else f"{ctx.prefix}blackjack 100"
            return await ctx.send(
                view=StatusPanel(
                    "قيمة الرهان مش مناسبة",
                    f"استخدم {usage}. أقل رهان **{MIN_BET:,}** وأقصى رهان **{MAX_BET:,}** {COIN}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        view = BlackjackView(self, ctx.author, amount)
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel("عندك جولة مفتوحة", "كمّل أو ألغِ اللعبة الحالية قبل بدء Blackjack.", "warning"),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            view.message = await ctx.send(view=view)
        except asyncio.CancelledError:
            release_solo_session(ctx.author.id, view)
            raise
        except Exception:
            release_solo_session(ctx.author.id, view)
            logger.exception("Could not create blackjack view requester_id=%s", ctx.author.id)
            raise


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
