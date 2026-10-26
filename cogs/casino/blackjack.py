"""Single-player blackjack backed by the durable virtual-currency wager ledger."""
# ruff: noqa: I001

import asyncio
import io
import logging
import secrets
import uuid

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay
from PIL import Image, ImageDraw, ImageFont

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.solo_betting import (
    COIN,
    MAX_BET,
    MIN_BET,
    claim_solo_session,
    recover_orphaned_casino_wagers,
    release_solo_session,
)
from cogs.utility.diagnostics import DiagnosticViewMixin
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



BLACKJACK_ART_FILENAME = "blackjack-table.png"
BLACKJACK_BRAND = "CKR"
_ART_WIDTH = 1200
_ART_HEIGHT = 620
_CARD_WIDTH = 124
_CARD_HEIGHT = 172
_CARD_STEP = 101


def _art_font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_card(draw, x: int, y: int, card, *, hidden: bool = False):
    """Draw a compact, readable card face or a patterned card back."""
    x2, y2 = x + _CARD_WIDTH, y + _CARD_HEIGHT
    if hidden or card is None:
        draw.rounded_rectangle(
            (x, y, x2, y2),
            radius=12,
            fill=(25, 43, 74),
            outline=(225, 190, 105),
            width=4,
        )
        draw.rounded_rectangle(
            (x + 9, y + 9, x2 - 9, y2 - 9),
            radius=8,
            outline=(144, 171, 210),
            width=2,
        )
        for offset in range(-_CARD_HEIGHT, _CARD_WIDTH, 18):
            draw.line(
                (x + max(10, offset), y + 12, min(x2 - 10, x + offset + _CARD_HEIGHT), y2 - 12),
                fill=(64, 91, 130),
                width=2,
            )
        draw.text(
            (x + _CARD_WIDTH // 2, y + _CARD_HEIGHT // 2),
            BLACKJACK_BRAND,
            fill=(242, 213, 150),
            font=_art_font(14),
            anchor="mm",
        )
        return

    rank, suit = card
    is_red = suit in ("♥", "♦")
    ink = (165, 45, 52) if is_red else (28, 35, 40)
    suit_letter = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}.get(suit, "?")
    draw.rounded_rectangle(
        (x, y, x2, y2),
        radius=12,
        fill=(243, 239, 225),
        outline=(210, 202, 181),
        width=3,
    )
    draw.text((x + 10, y + 7), str(rank), fill=ink, font=_art_font(26))
    draw.text((x + 12, y + 38), suit_letter, fill=ink, font=_art_font(17))
    draw.text(
        (x + _CARD_WIDTH // 2, y + _CARD_HEIGHT // 2 + 3),
        str(rank),
        fill=ink,
        font=_art_font(38),
        anchor="mm",
    )
    draw.rounded_rectangle(
        (x + 7, y + 7, x2 - 7, y2 - 7),
        radius=8,
        outline=(218, 211, 192),
        width=1,
    )


def _dealer_display_cards(dealer_cards, phase: str):
    """Hide every dealer card except the up-card while the player can still act."""
    cards = list(dealer_cards or [])
    if phase == "playing" and len(cards) > 1:
        return [(cards[0], False)] + [(None, True)] * (len(cards) - 1)
    return [(card, False) for card in cards] or [(None, True), (None, True)]


def render_blackjack_table(player_cards, dealer_cards, *, phase: str, amount: int) -> bytes:
    """Render a self-contained dark-felt blackjack table for the live V2 panel."""
    image = Image.new("RGB", (_ART_WIDTH, _ART_HEIGHT), (13, 24, 23))
    draw = ImageDraw.Draw(image)

    # Deep felt surface with a restrained gold frame.
    draw.rounded_rectangle(
        (20, 18, _ART_WIDTH - 20, _ART_HEIGHT - 18),
        radius=30,
        fill=(22, 64, 53),
        outline=(187, 148, 77),
        width=4,
    )
    draw.rounded_rectangle(
        (34, 32, _ART_WIDTH - 34, _ART_HEIGHT - 32),
        radius=24,
        outline=(59, 119, 96),
        width=2,
    )
    draw.text(
        (_ART_WIDTH // 2, 54),
        f"{BLACKJACK_BRAND} CASINO  /  BLACKJACK",
        fill=(245, 211, 145),
        font=_art_font(24),
        anchor="mm",
    )
    draw.text(
        (_ART_WIDTH // 2, 87),
        f"STAKE  {max(0, int(amount)):,} COINS",
        fill=(215, 229, 216),
        font=_art_font(17),
        anchor="mm",
    )

    phase_labels = {
        "waiting": "READY TO DEAL",
        "starting": "SHUFFLING / DEALING",
        "playing": "YOUR TURN",
        "settling": "REVEALING RESULT",
        "pending": "SETTLEMENT IN PROGRESS",
        "finished": "HAND COMPLETE",
    }
    draw.text(
        (_ART_WIDTH // 2, 119),
        phase_labels.get(phase, "BLACKJACK"),
        fill=(185, 205, 191),
        font=_art_font(15),
        anchor="mm",
    )

    # Dealer's cards: reveal only the up-card until the player's turn is over.
    dealer_row = _dealer_display_cards(dealer_cards, phase)

    player_row = [(card, False) for card in (player_cards or [])]
    if not player_row:
        player_row = [(None, True), (None, True)]

    def draw_row(cards, y, label):
        draw.text((60, y - 23), label, fill=(225, 234, 224), font=_art_font(16))
        count = len(cards)
        row_width = _CARD_WIDTH + _CARD_STEP * max(0, count - 1)
        start_x = max(50, (_ART_WIDTH - row_width) // 2)
        for index, (card, hidden) in enumerate(cards):
            _draw_card(draw, start_x + index * _CARD_STEP, y, card, hidden=hidden)

    draw_row(dealer_row, 145, "DEALER")
    draw.line((70, 339, _ART_WIDTH - 70, 339), fill=(102, 145, 112), width=2)
    draw_row(player_row, 370, "PLAYER")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _cards_text(cards):
    return "  ".join(f"[{rank}{suit}]" for rank, suit in cards) or "—"


class BlackjackView(DiagnosticViewMixin, LayoutView):
    """A one-message hand controlled only by its original player."""

    def __init__(self, cog, requester, amount, *, context=None, deck=None):
        super().__init__(timeout=180)
        self.cog = cog
        self.requester = requester
        self.context = context
        self.amount = amount
        self.original_amount = amount
        self.game_id = uuid.uuid4().hex
        self.deck = list(deck) if deck is not None else _new_shuffled_deck()
        self.player_cards = []
        self.dealer_cards = []
        self.message = None
        self.phase = "waiting"
        self.done = False
        self.resolving = False
        self.doubled_down = False
        self.result_published = False
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
        elements = [
            TextDisplay(self._content()),
            Separator(),
            MediaGallery(
                discord.MediaGalleryItem(
                    media=f"attachment://{BLACKJACK_ART_FILENAME}",
                    description="Blackjack table and current hand",
                )
            ),
            Separator(),
        ]
        if self.phase == "waiting":
            elements.append(ActionRow(
                self._button("deal", "ابدأ اللعب", self.start_game, "🃏"),
                self._button("cancel", "إلغاء", self.cancel, "❌"),
            ))
        elif self.phase == "playing":
            controls = [
                self._button("hit", "اسحب بطاقة", self.hit, "🎴"),
                self._button("stand", "قف", self.stand, "🛑"),
            ]
            if len(self.player_cards) == 2 and not self.doubled_down:
                controls.append(
                    self._button("double", "ضاعف الرهان", self.double_down, "2️⃣")
                )
            elements.append(ActionRow(*controls))
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
                "اسحب بطاقة أو قف. تقدر تضاعف الرهان قبل أول سحب؛ هتاخد بطاقة واحدة وبعدها الموزّع يكمل.\n"
                f"{f'-# {self.notice}' if self.notice else ''}"
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

    async def _art_file(self):
        image_bytes = await asyncio.to_thread(
            render_blackjack_table,
            list(self.player_cards),
            list(self.dealer_cards),
            phase=self.phase,
            amount=self.amount,
        )
        return discord.File(fp=io.BytesIO(image_bytes), filename=BLACKJACK_ART_FILENAME)

    async def _edit(self):
        if self.message is None:
            return
        self._render_view()
        try:
            await self.message.edit(view=self, attachments=[await self._art_file()])
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

    async def _double_down_wager(self):
        """Apply or reconcile the extra stake without ever charging it twice."""
        expected_stake = self.amount
        for attempt in range(3):
            try:
                result = await asyncio.to_thread(
                    bank.double_down_casino_wager,
                    self.game_id,
                    self.requester.id,
                    expected_stake,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Blackjack double-down transaction failed game_id=%s attempt=%s",
                    self.game_id,
                    attempt + 1,
                )
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
                    continue
                return {"doubled": False, "reason": "database_error"}

            if result.get("doubled"):
                return result

            # A prior call may have committed while its await was cancelled.
            # The expected-stake guard makes this response a safe receipt check.
            if (
                result.get("reason") == "stake_changed"
                and result.get("stake") == expected_stake * 2
            ):
                balance_after = None
                try:
                    balance_after = await asyncio.to_thread(
                        bank.get_balance,
                        self.requester.id,
                    )
                except Exception:
                    logger.exception(
                        "Could not read balance after reconciling double down game_id=%s",
                        self.game_id,
                    )
                return {
                    "doubled": True,
                    "stake": expected_stake * 2,
                    "additional_stake": expected_stake,
                    "balance_after": balance_after,
                    "reconciled": True,
                }

            if result.get("reason") in {
                "insufficient_funds",
                "stake_limit",
                "not_found",
                "not_owner",
                "unsupported_game",
                "not_active",
                "wallet_missing",
            }:
                return result

            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))

        return {"doubled": False, "reason": "database_error"}

    async def double_down(self, interaction):
        if (
            self.phase != "playing"
            or self.resolving
            or self.doubled_down
            or len(self.player_cards) != 2
        ):
            return await self._closed(interaction)

        self.resolving = True
        self.message = interaction.message or self.message
        self.notice = "جاري تأكيد الرهان الإضافي قبل سحب البطاقة..."
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.resolving = False
            self.notice = None
            return

        await self._edit()
        try:
            receipt = await self._double_down_wager()
        except asyncio.CancelledError:
            self.cog._schedule_recovery(
                self.game_id,
                force_refund=True,
                callback=self._recovered,
            )
            raise

        if not receipt.get("doubled"):
            reason = receipt.get("reason", "database_error")
            if reason == "insufficient_funds":
                detail = (
                    f"رصيدك الحالي **{int(receipt.get('balance', 0)):,} {COIN}** "
                    f"ومحتاج **{self.amount:,} {COIN}** إضافية."
                )
                self.resolving = False
                self.notice = "الرصيد مش كفاية لمضاعفة الرهان؛ تقدر تسحب بطاقة أو تقف."
                await self._edit()
                try:
                    await interaction.followup.send(
                        view=StatusPanel("رصيد غير كافٍ", detail, "warning"),
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    logger.debug(
                        "Could not send private double-down balance warning game_id=%s",
                        self.game_id,
                        exc_info=True,
                    )
                return
            if reason == "stake_limit":
                self.resolving = False
                self.notice = "قيمة الرهان وصلت للحد الآمن؛ كمل الجولة من غير مضاعفة."
                await self._edit()
                try:
                    await interaction.followup.send(
                        view=StatusPanel(
                            "تعذر مضاعفة الرهان",
                            "قيمة الرهان الجديدة هتتجاوز الحد الآمن للنظام.",
                            "warning",
                        ),
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    logger.debug("Could not send double-down limit warning", exc_info=True)
                return

            self.phase = "pending"
            self.notice = (
                "تعذر تأكيد الرهان المضاعف. قفلنا التحكم مؤقتًا وبنراجع العملية "
                "قبل ما نعلن نتيجة أو نسمح بحركة جديدة."
            )
            await self._edit()
            self.cog._schedule_recovery(
                self.game_id,
                force_refund=True,
                callback=self._recovered,
            )
            return

        self.amount = int(receipt["stake"])
        self.doubled_down = True
        self.notice = None
        # Standard double-down rules: exactly one card, then the dealer plays out.
        self.player_cards.append(self._draw())
        if _hand_value(self.player_cards) <= 21:
            while _hand_value(self.dealer_cards) < DEALER_STANDS_AT:
                self.dealer_cards.append(self._draw())
            kind = _final_outcome(self.player_cards, self.dealer_cards)
        else:
            kind = "loss"
        await self._settle_outcome(kind)

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
        return (
            f"player={_hand_value(self.player_cards)};dealer={_hand_value(self.dealer_cards)};"
            f"player_cards={player};dealer_cards={dealer};result={kind};"
            f"double_down={str(self.doubled_down).lower()};original_stake={self.original_amount}"
        )

    async def _apply_settlement(self, result):
        self.receipt = result
        self.phase = "finished"
        self.resolving = False
        self.done = True
        self.notice = None
        await self._edit()
        await self._publish_final_result()
        release_solo_session(self.requester.id, self)
        self.stop()

    async def _publish_final_result(self):
        """Publish a separate V2 result receipt while preserving the final table image."""
        if self.result_published or self.context is None or self.receipt is None:
            return
        self.result_published = True
        status = self.receipt.get("status")
        outcome = self.receipt.get("outcome")
        if status == "refunded" or outcome == "balance_limit":
            accent = "warning"
        elif outcome == "win":
            accent = "win"
        elif outcome == "push":
            accent = "neutral"
        else:
            accent = "loss"

        details = self._finished_content().split("\\n", 1)
        description = details[1] if len(details) > 1 else details[0]
        kwargs = {"allowed_mentions": discord.AllowedMentions.none()}
        if getattr(self.context, "interaction", None) is not None:
            kwargs["ephemeral"] = True
        try:
            await self.context.send(
                view=StatusPanel("Blackjack · النتيجة النهائية", description, accent),
                **kwargs,
            )
        except (discord.HTTPException, discord.InteractionResponded):
            logger.exception(
                "Could not publish separate Blackjack result game_id=%s",
                self.game_id,
            )

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
        amount = parse_bet_amount(bet, max_bet=MAX_BET)
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
        view = BlackjackView(self, ctx.author, amount, context=ctx)
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel("عندك جولة مفتوحة", "كمّل أو ألغِ اللعبة الحالية قبل بدء Blackjack.", "warning"),
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
