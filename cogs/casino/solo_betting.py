"""Shared solo betting games with durable economy settlement and Components V2 UI."""
# ruff: noqa: I001

import asyncio
import io
import logging
import secrets
import uuid
from collections.abc import Callable
from functools import partial

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.bet_validation import parse_bet_amount
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import THEME, StatusPanel

from cogs.casino.solo_betting_art import (
    _art_font,
    _draw_die,
    _draw_slot_symbol,
    _render_advanced_instant_art,
    _render_solo_game_art,
)
logger = logging.getLogger(__name__)

MIN_BET = 10
MAX_BET = 5_000
COIN = "🪙"
GROSS_WIN_MULTIPLIER_NUMERATOR = 190
GROSS_WIN_MULTIPLIER_DENOMINATOR = 100
SIDES = {
    "heads": {"label": "Heads", "emoji": "🪙"},
    "tails": {"label": "Tails", "emoji": "🪙"},
}
DICE_CHOICES = {str(number): {"label": str(number), "emoji": None} for number in range(1, 7)}
SLOT_SYMBOLS = ("🍒", "🍋", "🍊", "🍇", "⭐", "💎")
SLOT_TRIPLE_PAYOUTS = {"🍒": 500, "🍋": 600, "🍊": 700, "🍇": 800, "⭐": 1_000, "💎": 1_500}
SLOT_CHOICES = {"spin": {"label": "Spin", "emoji": "🎰"}}
_SOLO_GAME_ACCENTS = {
    "coinflip": THEME["gold"],
    "dice": THEME["teal"],
    "slots": THEME["red_dark"],
}
_RECOVERY_DELAYS_SECONDS = (2, 5, 15, 30, 60)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_SESSIONS: dict[int, discord.ui.View] = {}


def has_active_solo_session(user_id: int) -> bool:
    """Whether this member already has an open solo-game session."""
    current = _ACTIVE_SESSIONS.get(int(user_id))
    return current is not None and not getattr(current, "done", False) and not current.is_finished()


def claim_solo_session(user_id: int, view: discord.ui.View) -> bool:
    """Atomically reserve the member's one active solo-game session."""
    user_id = int(user_id)
    if has_active_solo_session(user_id):
        return False
    _ACTIVE_SESSIONS[user_id] = view
    return True


def release_solo_session(user_id: int, view: discord.ui.View) -> None:
    """Release a reserved session without clearing a newer session."""
    if _ACTIVE_SESSIONS.get(int(user_id)) is view:
        _ACTIVE_SESSIONS.pop(int(user_id), None)



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


_STARTUP_PANEL_TASKS: set[asyncio.Task] = set()
_GAME_TITLES = {
    "coinflip": "COIN FLIP",
    "dice": "DICE",
    "slots": "SLOTS",
    "blackjack": "Blackjack",
    "mines": "Mines",
    "crash": "Crash",
    "plinko": "Plinko",
    "number_roulette": "NUMBER ROULETTE",
    "hilo": "Hi-Lo",
}


async def _publish_interrupted_wager_panel(bot, record, receipt):
    """Replace a stale game panel after the matching wager has been safely refunded."""
    if (
        receipt.get("status") != "refunded"
        or receipt.get("outcome") != "refund"
    ):
        return
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
        title = _GAME_TITLES.get(str(record.get("game_type")), "GAME")
        stake = int(record.get("stake", receipt.get("stake", 0)))
        coin_emoji = get_coin_emoji(getattr(channel, "guild", None))
        await message.edit(
            view=StatusPanel(
                "ROUND REFUNDED",
                (
                    f"Your **{title}** round was interrupted. "
                    f"Bet returned: **{stake:,} {coin_emoji}**. No result was recorded."
                ),
                "warning",
            )
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logger.info(
            "Interrupted wager refunded but original panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logger.warning(
            "Interrupted wager refunded but the bot cannot access its panel game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logger.warning(
            "Interrupted wager refunded but its old panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )
    except Exception:
        logger.exception(
            "Interrupted wager was refunded, but an unexpected error blocked panel cleanup game_id=%s",
            record.get("game_id"),
        )


async def recover_orphaned_casino_wagers(bot, schedule_recovery):
    """Refund unfinished solo bets at startup and close their stale Discord panels."""
    records = await asyncio.to_thread(bank.active_casino_wager_records)
    refunded = await asyncio.to_thread(bank.refund_orphaned_casino_wagers)
    active_ids = await asyncio.to_thread(bank.active_casino_wager_ids)
    active_set = set(active_ids)
    records_by_id = {str(record["game_id"]): record for record in records}

    for game_id, record in records_by_id.items():
        has_panel = bool(record.get("channel_id") and record.get("message_id"))
        if game_id in active_set:
            callback = (
                partial(_publish_interrupted_wager_panel, bot, record)
                if has_panel
                else None
            )
            schedule_recovery(game_id, force_refund=True, callback=callback)
            continue

        receipt = await asyncio.to_thread(bank.casino_wager_status, game_id)
        if (
            has_panel
            and receipt is not None
            and receipt.get("status") == "refunded"
            and receipt.get("outcome") == "refund"
        ):
            task = asyncio.create_task(
                _publish_interrupted_wager_panel(bot, record, receipt)
            )
            _STARTUP_PANEL_TASKS.add(task)
            task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

    for game_id in active_ids:
        if str(game_id) not in records_by_id:
            schedule_recovery(game_id, force_refund=True)

    return refunded, active_ids


class SoloWagerView(DiagnosticViewMixin, LayoutView):
    """Requester-bound solo game with a distinct final receipt and durable settlement."""

    def __init__(
        self,
        cog,
        requester,
        amount: int,
        *,
        game_type: str = "coinflip",
        game_title: str = "COIN FLIP",
        choices: dict | None = None,
        payout_numerator: int = GROSS_WIN_MULTIPLIER_NUMERATOR,
        payout_denominator: int = GROSS_WIN_MULTIPLIER_DENOMINATOR,
        odds_description: str = "50%",
        instructions: str = "Choose heads or tails.",
        payout_description: str | None = None,
        outcome_resolver: Callable[[str, int], tuple[str, bool, int]] | None = None,
        context=None,
    ):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester = requester
        self.coin_emoji = get_coin_emoji(getattr(requester, "guild", None))
        self.context = context
        self.amount = amount
        self.result_published = False
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
        art_types = {"coinflip", "dice", "slots", "plinko", "number_roulette"}
        self._art_filename = f"{game_type}.png" if game_type in art_types else None
        if game_type in {"plinko", "number_roulette"}:
            self._art_bytes = _render_advanced_instant_art(game_type)
        else:
            self._art_bytes = _render_solo_game_art(game_type) if self._art_filename is not None else None
        self._buttons = {}
        self.selected_number = None
        self._confirm_button = None
        self._replay_button = None
        self._panel_text = TextDisplay(self._waiting_content())

        choice_buttons = []
        number_selects = []
        for choice, metadata in self.choices.items():
            if self.game_type == "number_roulette":
                continue
            button = discord.ui.Button(
                label=metadata["label"],
                style=discord.ButtonStyle.secondary,
                custom_id=f"{self.game_type}_{choice}",
            )
            button.callback = partial(self.choose, side=choice)
            self._buttons[choice] = button
            choice_buttons.append(button)

        if self.game_type == "number_roulette":
            for lower, upper, placeholder in (
                (0, 18, "Choose 0–18"),
                (19, 36, "Choose 19–36"),
            ):
                options = [
                    discord.SelectOption(
                        label=self.choices[str(number)]["label"],
                        value=str(number),
                    )
                    for number in range(lower, upper + 1)
                ]
                selector = discord.ui.Select(
                    placeholder=placeholder,
                    min_values=1,
                    max_values=1,
                    options=options,
                    custom_id=f"number_roulette_{lower}_{upper}",
                )
                async def choose_number(interaction, current_selector=selector):
                    self.selected_number = current_selector.values[0]
                    self._confirm_button.disabled = False
                    self._panel_text.content = self._waiting_content()
                    await interaction.response.edit_message(view=self)
                selector.callback = choose_number
                self._buttons[f"numbers_{lower}_{upper}"] = selector
                number_selects.append(selector)

            self._confirm_button = discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.secondary,
                custom_id="number_roulette_confirm",
                disabled=True,
            )
            self._confirm_button.callback = self.confirm_number
            self._buttons["confirm"] = self._confirm_button

        cancel = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_cancel",
        )
        cancel.callback = self.cancel
        self._buttons["cancel"] = cancel

        elements = [self._panel_text]
        if self._art_filename is not None:
            elements.append(
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{self._art_filename}",
                        description=f"{self.game_title} artwork",
                    )
                )
            )
        elements.append(Separator())
        if self.game_type == "number_roulette":
            elements.append(ActionRow(number_selects[0]))
            elements.append(ActionRow(number_selects[1]))
            elements.append(ActionRow(self._confirm_button, cancel))
        elif len(choice_buttons) <= 2:
            elements.append(ActionRow(*choice_buttons, cancel))
        else:
            split = (len(choice_buttons) + 1) // 2
            elements.append(ActionRow(*choice_buttons[:split]))
            elements.append(ActionRow(*choice_buttons[split:], cancel))
        self.add_item(
            Container(
                *elements,
                accent_color=_SOLO_GAME_ACCENTS.get(self.game_type, THEME["teal"]),
            )
        )

    def _waiting_content(self) -> str:
        content = f"# {self.game_title}\n**Bet:** {self.amount:,} {self.coin_emoji}"
        if self.instructions:
            content += f"\n{self.instructions}"
        if self.odds_description:
            content += f"\n**Chance:** {self.odds_description}"
        if self.payout_description:
            content += f"\n-# {self.payout_description}"
        if self.game_type == "number_roulette":
            selected = self.selected_number if self.selected_number is not None else "—"
            content += f"\n**Selected:** {selected}"
        return content

    def _art_file(self):
        if self._art_filename is None or self._art_bytes is None:
            return None
        return discord.File(io.BytesIO(self._art_bytes), filename=self._art_filename)

    def _update_result_art(self, choice: str, result: str, outcome: str) -> None:
        if self._art_filename is None:
            return
        won = True if outcome == "win" else False if outcome == "loss" else None
        if self.game_type in {"plinko", "number_roulette"}:
            self._art_bytes = _render_advanced_instant_art(
                self.game_type,
                choice=choice,
                result=result,
                won=won,
            )
        else:
            self._art_bytes = _render_solo_game_art(
                self.game_type,
                choice=choice if self.game_type in {"coinflip", "dice"} else None,
                result=result,
                won=won,
            )

    def _disable_buttons(self) -> None:
        for button in self._buttons.values():
            button.disabled = True

    def _render_result_panel(self) -> None:
        self.clear_items()
        self._panel_text = TextDisplay(self._panel_text.content)
        self._replay_button = discord.ui.Button(
            label="Play Again",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_replay",
        )
        self._replay_button.callback = self.replay
        elements = [self._panel_text]
        if self._art_filename is not None:
            elements.append(
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{self._art_filename}",
                        description=f"{self.game_title} result",
                    )
                )
            )
        elements.extend((Separator(), ActionRow(self._replay_button)))
        self.add_item(
            Container(
                *elements,
                accent_color=_SOLO_GAME_ACCENTS.get(self.game_type, THEME["teal"]),
            )
        )
        self._buttons = {"replay": self._replay_button}

    async def confirm_number(self, interaction: discord.Interaction) -> None:
        if self.selected_number is None:
            return await interaction.response.send_message(
                view=StatusPanel("Choose a number", "Select a number first.", "warning"),
                ephemeral=True,
            )
        await self.choose(interaction, side=self.selected_number)

    async def replay(self, interaction: discord.Interaction) -> None:
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
                view=StatusPanel("Balance unavailable", "Try again in a moment.", "warning"),
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
        replay_view = SoloWagerView(
            self.cog,
            self.requester,
            self.amount,
            game_type=self.game_type,
            game_title=self.game_title,
            choices=self.choices,
            payout_numerator=self.payout_numerator,
            payout_denominator=self.payout_denominator,
            odds_description=self.odds_description,
            instructions=self.instructions,
            payout_description=self.payout_description,
            outcome_resolver=self.outcome_resolver,
            context=self.context,
        )
        if not claim_solo_session(self.requester.id, replay_view):
            replay_view.stop()
            return await interaction.response.send_message(
                view=StatusPanel("Game active", "Finish your current solo game first.", "warning"),
                ephemeral=True,
            )
        replay_view.message = interaction.message or self.message
        replay_file = replay_view._art_file()
        try:
            await interaction.response.edit_message(
                view=replay_view,
                attachments=[replay_file] if replay_file is not None else [],
            )
            self.stop()
        except asyncio.CancelledError:
            release_solo_session(self.requester.id, replay_view)
            raise
        except Exception:
            release_solo_session(self.requester.id, replay_view)
            logger.exception("Could not restart solo game type=%s", self.game_type)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    view=StatusPanel("Replay unavailable", "Start a new game with the command.", "warning"),
                    ephemeral=True,
                )

    async def _edit_message(self) -> bool:
        if self.message is None:
            return False
        try:
            if self._art_filename is not None:
                await self.message.edit(view=self, attachments=[self._art_file()])
            else:
                await self.message.edit(view=self)
            return True
        except discord.NotFound:
            logger.info("Solo wager panel was deleted game_id=%s", self.game_id)
        except discord.HTTPException:
            logger.warning("Could not update solo wager panel game_id=%s", self.game_id, exc_info=True)
        return False

    def _release_session(self) -> None:
        release_solo_session(self.requester.id, self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "Not your game",
                    "Only the original player can play this round.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def choose(self, interaction: discord.Interaction, side: str) -> None:
        if self.done or self.resolving:
            return await interaction.response.send_message(
                view=StatusPanel("Game closed", "This round has already ended.", "warning"),
                ephemeral=True,
            )
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                view=StatusPanel("Not your game", "Only the original player can play this round.", "warning"),
                ephemeral=True,
            )

        self.resolving = True
        self.message = interaction.message or self.message
        self._disable_buttons()
        try:
            await interaction.response.defer()
        except asyncio.CancelledError:
            self.done = True
            self.resolving = False
            self._disable_buttons()
            self._release_session()
            self.stop()
            raise
        except (discord.HTTPException, discord.InteractionResponded):
            logger.exception(
                "Could not acknowledge solo wager choice game_type=%s requester_id=%s",
                self.game_type,
                self.requester.id,
            )
            await self._finish_without_bet(
                "# Could not start\nNo bet was placed. Try again."
            )
            return

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
                "# Result unavailable\n"
                "No bet was placed. Try again."
            )
            return
        self.game_id = game_id

        try:
            wager_args = (
                game_id,
                self.requester.id,
                self.game_type,
                self.amount,
                side,
                result_side,
                won,
                payout,
            )
            channel_id = getattr(interaction, "channel_id", None)
            message_id = getattr(self.message, "id", None)
            if isinstance(channel_id, int) and isinstance(message_id, int):
                receipt = await asyncio.to_thread(
                    bank.play_casino_wager,
                    *wager_args,
                    channel_id=channel_id,
                    message_id=message_id,
                )
            else:
                receipt = await asyncio.to_thread(
                    bank.play_casino_wager,
                    *wager_args,
                )
        except asyncio.CancelledError:
            # The database operation may have committed before cancellation arrived.
            # Instant games never commit an active wager, so lookup cannot refund a win.
            self.cog._schedule_recovery(
                game_id, force_refund=False, callback=self._recovered
            )
            raise
        except Exception:
            logger.exception("Could not confirm atomic solo wager game_id=%s", game_id)
            self._panel_text.content = (
                "# PROCESSING\n"
                "Finalizing this transaction..."
            )
            await self._edit_message()
            self.cog._schedule_recovery(
                game_id, force_refund=False, callback=self._recovered
            )
            return

        if not receipt.get("created"):
            reason = receipt.get("reason")
            if reason == "insufficient_funds":
                balance = int(receipt.get("balance", 0))
                message = f"Balance: **{balance:,} {self.coin_emoji}** · Bet: **{self.amount:,} {self.coin_emoji}**."
            elif reason == "duplicate_game":
                message = "This round was already recorded."
            else:
                message = "The bet could not be accepted. No coins were taken."
            return await self._finish_without_bet("# Bet declined\n" + message)

        if not receipt.get("settled"):
            # Defensive fail-closed guard: an instant bet must return a terminal receipt.
            self._panel_text.content = (
                "# PROCESSING\n"
                "Finalizing this transaction..."
            )
            await self._edit_message()
            self.cog._schedule_recovery(
                game_id, force_refund=False, callback=self._recovered
            )
            return

        await self._finalize(receipt)

    async def _recover_after_component_error(self, incident_id: str) -> None:
        """Resolve a callback fault without refunding a successfully settled instant bet."""
        self.resolving = True
        self._disable_buttons()
        if self.game_id is None:
            await self._finish_without_bet(
                "# Game closed\n"
                f"Reference: {incident_id} · No bet was placed."
            )
            return

        self._panel_text.content = (
            "# Checking bet\n"
            f"Reference: {incident_id}\n"
            "Verifying the same transaction."
        )
        self.cog._schedule_recovery(
            self.game_id,
            force_refund=False,
            callback=self._recovered,
        )
        await self._edit_message()

    async def _finish_without_bet(self, content: str) -> None:
        self.done = True
        self.resolving = False
        self._panel_text.content = content
        self._disable_buttons()
        try:
            await self._edit_message()
        finally:
            self._release_session()
            self.stop()

    async def _recovered(self, result: dict) -> None:
        if result.get("settled"):
            await self._finalize(result)
        elif result.get("reason") == "not_found":
            await self._finish_without_bet(
                "# Bet not confirmed\n"
                "No result was announced. Check your balance before retrying."
            )

    async def _finalize(self, result: dict) -> None:
        self.done = True
        self.resolving = False
        self._disable_buttons()
        choice = str(result.get("choice", ""))
        result_side = str(result.get("result", ""))
        choice_label = self.choices.get(choice, {}).get("label", choice)
        result_label = self.choices.get(result_side, {}).get("label", result_side)
        if self.game_type == "plinko" and result_side.startswith("slot="):
            try:
                parts = dict(part.split("=", 1) for part in result_side.split(";") if "=" in part)
                result_label = f"Bin {int(parts['slot']) + 1} · {parts.get('multiplier', '0x')}"
            except (KeyError, ValueError):
                result_label = "Ball dropped"
        balance_after = result.get("balance_after")
        balance_line = (
            f"\n**Balance:** {int(balance_after):,} {self.coin_emoji}"
            if balance_after is not None
            else ""
        )
        outcome = result.get("outcome", "loss")
        stake = int(result.get("stake", self.amount))
        bonus = max(0, int(result.get("payout", 0)) - stake)
        self._update_result_art(choice, result_side, outcome)

        if result.get("status") == "refunded" or outcome == "balance_limit":
            self._panel_text.content = (
                f"# REFUNDED · {self.game_title}\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0"
                f"{balance_line}"
            )
        elif outcome == "win":
            self._panel_text.content = (
                f"# WIN · {self.game_title}\n"
                f"Pick: **{choice_label}** · Result: **{result_label}**\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: **+{bonus:,} {self.coin_emoji}**"
                f"{balance_line}"
            )
        elif outcome == "push":
            self._panel_text.content = (
                f"# PUSH · {self.game_title}\n"
                f"Pick: **{choice_label}** · Result: **{result_label}**\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0"
                f"{balance_line}"
            )
        else:
            self._panel_text.content = (
                f"# LOSS · {self.game_title}\n"
                f"Pick: **{choice_label}** · Result: **{result_label}**\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0"
                f"{balance_line}"
            )

        self._render_result_panel()
        try:
            updated = await self._edit_message()
            self.result_published = updated
            if not updated:
                logger.error("Could not publish settled solo result in-place game_id=%s", self.game_id)
        finally:
            self._release_session()

    async def cancel(self, interaction: discord.Interaction) -> None:
        if self.done or self.resolving:
            return await interaction.response.send_message(
                view=StatusPanel("Game closed", "This round can no longer be cancelled.", "warning"),
                ephemeral=True,
            )
        self.message = interaction.message or self.message
        self.done = True
        self._disable_buttons()
        self._panel_text.content = (
            f"# CANCELLED · {self.game_title}\n"
            "No bet was placed."
        )
        try:
            await interaction.response.edit_message(view=self)
        except asyncio.CancelledError:
            raise
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning(
                "Could not cancel solo game through its interaction game_type=%s",
                self.game_type,
                exc_info=True,
            )
            await self._edit_message()
        finally:
            self._release_session()
            self.stop()

    async def on_timeout(self) -> None:
        if self.done:
            if self._replay_button is not None:
                self._replay_button.disabled = True
                await self._edit_message()
            self.stop()
            return
        if self.resolving:
            return
        self.done = True
        self._disable_buttons()
        self._panel_text.content = (
            f"# EXPIRED · {self.game_title}\n"
            "No bet was placed."
        )
        try:
            await self._edit_message()
        finally:
            self._release_session()
            self.stop()


class SoloBetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._wagers_ready = False

    async def cog_load(self):
        try:
            refunded, active_ids = await recover_orphaned_casino_wagers(
                self.bot,
                self._schedule_recovery,
            )
        except Exception:
            self._wagers_ready = False
            logger.exception(
                "Solo casino wager recovery could not be verified; disabling new wagers"
            )
            return

        self._wagers_ready = True
        if refunded:
            logger.warning("Refunded %s interrupted solo casino wager(s) during startup", refunded)
        if active_ids:
            logger.warning("Scheduled recovery for %s unresolved solo casino wager(s)", len(active_ids))

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
                    "Bets unavailable",
                    "Wagers are being checked. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        amount = parse_bet_amount(bet, max_bet=MAX_BET)
        if not MIN_BET <= amount <= MAX_BET:
            usage = f"/{command_name} bet:100" if ctx.interaction is not None else f"{ctx.prefix}{command_name} 100"
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
                    "Finish or cancel your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        try:
            balance = await asyncio.to_thread(bank.get_balance, int(ctx.author.id))
        except Exception:
            logger.exception(
                "Could not verify solo wager balance requester_id=%s game_type=%s",
                ctx.author.id,
                game_type,
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
            context=ctx,
        )
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel(
                    "Game active",
                    "Finish or cancel your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            if view._art_filename is not None:
                view.message = await ctx.send(view=view, file=view._art_file())
            else:
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
        aliases=["flip", "cf", "coin", "coin-flip", "عملة", "رمي_العملة"],
        description="راهن بعملة افتراضية على نتيجة رمي العملة",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def coinflip(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="coinflip",
            game_title="COIN FLIP",
            choices=SIDES,
            payout_numerator=190,
            odds_description="50%",
            instructions="Choose heads or tails.",
            command_name="coinflip",
            payout_description="Bonus: +0.9x.",
        )

    @commands.hybrid_command(
        name="dice",
        aliases=["نرد", "زهر", "roll", "die"],
        description="راهن بعملة افتراضية على رقم النرد",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def dice(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="dice",
            game_title="DICE",
            choices=DICE_CHOICES,
            payout_numerator=570,
            odds_description="16.67%",
            instructions="Pick a number from 1 to 6.",
            payout_description="Bonus: +4.7x.",
            command_name="dice",
        )

    @commands.hybrid_command(
        name="slots",
        aliases=["slot", "سلوت", "ماكينة", "spin"],
        description="لف ماكينة الحظ مقابل رهان بعملة افتراضية",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def slots(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="slots",
            game_title="SLOTS",
            choices=SLOT_CHOICES,
            payout_numerator=170,
            odds_description="44.44% any win",
            instructions="Spin 3 reels.",
            command_name="slots",
            payout_description="Pair: +0.7x · Trips: +4x–14x bonus.",
            outcome_resolver=_spin_slots,
        )


async def setup(bot):
    await bot.add_cog(SoloBetting(bot))
