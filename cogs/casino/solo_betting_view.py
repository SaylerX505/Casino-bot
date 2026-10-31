"""Components V2 view and interaction lifecycle for instant solo wagers."""

import asyncio
import io
import logging
import secrets
import uuid
from collections.abc import Callable
from functools import partial

import discord
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.solo_betting_art import (
    _render_advanced_instant_art,
    _render_solo_game_art,
)
from cogs.casino.solo_betting_core import (
    COIN,
    GROSS_WIN_MULTIPLIER_DENOMINATOR,
    GROSS_WIN_MULTIPLIER_NUMERATOR,
    SIDES,
    _SOLO_GAME_ACCENTS,
    _gross_payout,
    claim_solo_session,
    release_solo_session,
)
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import THEME, StatusPanel

logger = logging.getLogger(__name__)


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
            # Recover commits after cancellation.
            # Instant games never hold wagers.
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
            # Require terminal instant receipts.
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
