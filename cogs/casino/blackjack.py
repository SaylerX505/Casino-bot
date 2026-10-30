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

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.solo_betting import (
    MAX_BET,
    MIN_BET,
    claim_solo_session,
    recover_orphaned_casino_wagers,
    release_solo_session,
)
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import THEME, StatusPanel

from cogs.casino.blackjack_art import (
    BLACKJACK_ART_FILENAME,
    BLACKJACK_BRAND,
    _dealer_display_cards,
    render_blackjack_table,
)
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
    """A private-control blackjack table with a separately published final receipt."""

    def __init__(self, cog, requester, amount, *, context=None, deck=None):
        super().__init__(timeout=180)
        self.cog = cog
        self.requester = requester
        guild = getattr(context, "guild", None) or getattr(requester, "guild", None)
        self.coin_emoji = get_coin_emoji(guild)
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
        self.result_kind = None
        self.receipt = None
        self.notice = None
        self._replay_button = None
        self._replay_expired = False
        self._art_cache_key = None
        self._art_cache_bytes = None
        self._buttons = {}
        self._render_view()

    async def interaction_check(self, interaction):
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
        return True

    def _button(self, key, label, callback):
        button = discord.ui.Button(
            label=label,
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
                self._button("deal", "Deal", self.start_game),
                self._button("cancel", "Cancel", self.cancel),
            ))
        elif self.phase == "playing":
            controls = [
                self._button("hit", "Hit", self.hit),
                self._button("stand", "Stand", self.stand),
            ]
            if len(self.player_cards) == 2 and not self.doubled_down:
                controls.append(
                    self._button("double", "Double", self.double_down)
                )
            elements.append(ActionRow(*controls))
        elif self.phase == "finished" and self.receipt is not None:
            self._replay_button = self._button("replay", "Play Again", self.replay)
            self._replay_button.disabled = self._replay_expired
            elements.append(ActionRow(self._replay_button))
        self.add_item(Container(*elements, accent_color=THEME["gold"]))

    async def replay(self, interaction):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                view=StatusPanel("Not your game", "Only the original player can replay.", "warning"),
                ephemeral=True,
            )
        if self.phase != "finished" or self.receipt is None or self.context is None:
            return await self._closed(interaction)
        try:
            balance = await asyncio.to_thread(bank.get_balance, int(self.requester.id))
        except Exception:
            logger.exception("Could not verify Blackjack replay balance user_id=%s", self.requester.id)
            return await interaction.response.send_message(
                view=StatusPanel("Balance unavailable", "Try again shortly.", "warning"),
                ephemeral=True,
            )
        if balance < self.original_amount:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "Not enough coins",
                    f"Balance: {balance:,} {self.coin_emoji} · Bet: {self.original_amount:,} {self.coin_emoji}",
                    "warning",
                ),
                ephemeral=True,
            )
        replay_view = BlackjackView(
            self.cog, self.requester, self.original_amount, context=self.context
        )
        if not claim_solo_session(self.requester.id, replay_view):
            replay_view.stop()
            return await interaction.response.send_message(
                view=StatusPanel("Game active", "Finish your current solo game first.", "warning"),
                ephemeral=True,
            )
        replay_view.message = interaction.message or self.message
        try:
            await interaction.response.edit_message(
                view=replay_view,
                attachments=[await replay_view._art_file()],
            )
            self.stop()
        except asyncio.CancelledError:
            release_solo_session(self.requester.id, replay_view)
            raise
        except Exception:
            release_solo_session(self.requester.id, replay_view)
            logger.exception("Could not restart Blackjack game_id=%s", self.game_id)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    view=StatusPanel("Replay unavailable", "Start a new game with /blackjack.", "warning"),
                    ephemeral=True,
                )

    async def _recover_after_component_error(self, incident_id: str) -> None:
        """Refund an unfinished hand following an unexpected UI callback exception."""
        if self.phase == "waiting" and not self.resolving:
            await self._finish_without_bet(
                f"Game closed. Ref: {incident_id}. No bet was placed."
            )
            return

        self.resolving = True
        self.phase = "pending"
        self.notice = (
            f"Game error (reference {incident_id}). "
            "Checking the wager; unfinished rounds are refunded."
        )
        self.cog._schedule_recovery(
            self.game_id,
            force_refund=True,
            callback=self._recovered,
        )
        await self._edit()

    def _content(self):
        if self.phase == "waiting":
            return f"# BLACKJACK\n**Bet:** {self.amount:,} {self.coin_emoji}"
        if self.phase == "starting":
            return f"# BLACKJACK\n**Bet:** {self.amount:,} {self.coin_emoji} · Dealing..."
        if self.phase == "playing":
            notice = f"\n-# {self.notice}" if self.notice else ""
            return (
                f"# BLACKJACK\n**Bet:** {self.amount:,} {self.coin_emoji}\n"
                f"**Dealer:** {_cards_text(self.dealer_cards[:1])}  [🂠]\n"
                f"**You:** {_cards_text(self.player_cards)} · **{_hand_value(self.player_cards)}**\n"
                f"-# Hit · Stand · Double (before first hit){notice}"
            )
        if self.phase in ("settling", "pending"):
            label = "Checking result..." if self.phase == "settling" else "Settlement pending..."
            return (
                f"# BLACKJACK · {label}\n"
                f"You: {_hand_value(self.player_cards)} · Dealer: {_hand_value(self.dealer_cards)}\n"
                f"{self.notice or 'Verifying the result.'}"
            )
        return self._finished_content()

    def _finished_content(self):
        receipt = self.receipt
        if receipt is None:
            return f"# BLACKJACK\n{self.notice or 'Game ended.'} · No bet was placed."
        outcome = str(receipt.get("outcome") or "")
        stake = int(receipt.get("stake", self.amount))
        bonus = max(0, int(receipt.get("payout", 0)) - stake)
        balance_after = receipt.get("balance_after")
        balance_line = f"\n**Balance:** {int(balance_after):,} {self.coin_emoji}" if balance_after is not None else ""
        if receipt.get("status") == "refunded" or outcome in {"refund", "balance_limit"}:
            heading, bonus_line = "REFUNDED", "Bonus: +0"
        elif outcome == "push":
            heading, bonus_line = "PUSH", "Bonus: +0"
        elif outcome == "win":
            heading = "BLACKJACK" if self.result_kind == "natural" else "WIN"
            bonus_line = f"Bonus: **+{bonus:,} {self.coin_emoji}**"
        else:
            heading, bonus_line = "LOSS", "Bonus: +0"
        return (
            f"# {heading} · BLACKJACK\n"
            f"**You:** {_cards_text(self.player_cards)} · {_hand_value(self.player_cards)}\n"
            f"**Dealer:** {_cards_text(self.dealer_cards)} · {_hand_value(self.dealer_cards)}\n"
            f"Bet: {stake:,} {self.coin_emoji} · {bonus_line}{balance_line}"
        )

    async def _art_file(self):
        cache_key = (
            tuple(self.player_cards),
            tuple(self.dealer_cards),
            self.phase,
            int(self.amount),
        )
        if cache_key != self._art_cache_key or self._art_cache_bytes is None:
            image_bytes = await asyncio.to_thread(
                render_blackjack_table,
                list(self.player_cards),
                list(self.dealer_cards),
                phase=self.phase,
                amount=self.amount,
            )
            self._art_cache_key = cache_key
            self._art_cache_bytes = image_bytes
        return discord.File(
            fp=io.BytesIO(self._art_cache_bytes),
            filename=BLACKJACK_ART_FILENAME,
        )

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
            view=StatusPanel("Game closed", "Start a new round with /blackjack.", "warning"),
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
            self.notice = "Checking bet status before announcing a result."
            await self._edit()
            self.cog._schedule_recovery(self.game_id, force_refund=True, callback=self._recovered)
            return
        if not created.get("created"):
            if created.get("reason") == "insufficient_funds":
                notice = f"Balance: {int(created.get('balance', 0)):,} {self.coin_emoji} · Bet: {self.amount:,}."
            else:
                notice = "Bet declined. No coins were taken."
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
        self.notice = None
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.resolving = False
            self.notice = None
            return

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
                    f"Balance: **{int(receipt.get('balance', 0)):,} {self.coin_emoji}** "
                    f"· Extra bet: **{self.amount:,} {self.coin_emoji}**."
                )
                self.resolving = False
                self.notice = "Not enough coins to double. Hit or stand."
                await self._edit()
                try:
                    await interaction.followup.send(
                        view=StatusPanel("Not enough coins", detail, "warning"),
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
                self.notice = "Bet limit reached. Continue without doubling."
                await self._edit()
                try:
                    await interaction.followup.send(
                        view=StatusPanel(
                            "Cannot double",
                            "The new bet would exceed the allowed limit.",
                            "warning",
                        ),
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    logger.debug("Could not send double-down limit warning", exc_info=True)
                return

            self.phase = "pending"
            self.notice = (
                "Could not confirm the extra bet. Checking the transaction. "
                "before announcing a result or allowing another move."
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
        self.notice = None
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
            self.notice = "Result ready. Retrying settlement safely."
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
        release_solo_session(self.requester.id, self)

    async def _recovered(self, result):
        if result.get("settled"):
            await self._apply_settlement(result)
        elif result.get("reason") == "not_found":
            await self._finish_without_bet("Bet could not be confirmed. No result was announced.")

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
        self.notice = "Cancelled before dealing. No bet was placed."
        self.message = interaction.message or self.message
        self._render_view()
        try:
            await interaction.response.edit_message(
                view=self,
                attachments=[await self._art_file()],
            )
        except discord.HTTPException:
            logger.warning(
                "Could not refresh cancelled Blackjack art game_id=%s",
                self.game_id,
                exc_info=True,
            )
            if not interaction.response.is_done():
                try:
                    await interaction.response.send_message(
                        view=StatusPanel(
                            "Round cancelled",
                            "No bet was placed. The old controls are closed.",
                            "warning",
                        ),
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    logger.debug("Could not acknowledge cancelled Blackjack fallback", exc_info=True)
        release_solo_session(self.requester.id, self)
        self.stop()

    async def on_timeout(self):
        if self.done:
            if self._replay_button is not None:
                self._replay_expired = True
                await self._edit()
            self.stop()
            return
        if self.resolving:
            return
        if self.phase == "waiting":
            await self._finish_without_bet("Start timed out. No bet was placed.")
            return
        if self.phase != "playing":
            return
        self.resolving = True
        self.phase = "pending"
        self.notice = "Time expired. The unfinished bet is being returned."
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
        aliases=["bj", "black-jack", "بلجاك", "بلاكجاك"],
        description="Play Blackjack with virtual coins",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def blackjack(self, ctx, bet: str):
        if not self._wagers_ready:
            return await ctx.send(
                view=StatusPanel(
                    "Blackjack unavailable",
                    "Wagers are being checked. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        amount = parse_bet_amount(bet, max_bet=MAX_BET)
        if not MIN_BET <= amount <= MAX_BET:
            usage = "/blackjack bet:100" if ctx.interaction is not None else f"{ctx.prefix}blackjack 100"
            return await ctx.send(
                view=StatusPanel(
                    "Invalid bet",
                    f"Use {usage}. Bet: {MIN_BET:,}–{MAX_BET:,} {get_coin_emoji(getattr(ctx, 'guild', None))}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            balance = await asyncio.to_thread(bank.get_balance, int(ctx.author.id))
        except Exception:
            logger.exception(
                "Could not verify blackjack balance requester_id=%s",
                ctx.author.id,
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
        view = BlackjackView(self, ctx.author, amount, context=ctx)
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel("Game active", "Finish or cancel your current solo game first.", "warning"),
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
