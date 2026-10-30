"""UI regression tests for the single-player coinflip session."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ui import LayoutView

from cogs.casino import solo_betting as solo_betting_module
from cogs.casino.solo_betting import (
    DICE_CHOICES,
    SIDES,
    SLOT_CHOICES,
    SoloWagerView,
    _spin_slots,
    claim_solo_session,
    has_active_solo_session,
)
from cogs.utility.theme import StatusPanel


class SoloWagerViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requester = SimpleNamespace(id=101, mention="<@101>")
        self.cog = SimpleNamespace()
        self.view = SoloWagerView(self.cog, self.requester, 100)

    async def asyncTearDown(self):
        self.view.stop()

    def test_coinflip_uses_v2_layout_and_clear_primary_actions(self):
        self.assertIsInstance(self.view, LayoutView)
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(len(buttons), 3)
        self.assertEqual({button.label for button in buttons}, {"Heads", "Tails", "Cancel"})
        self.assertEqual(
            {button.style for button in buttons},
            {discord.ButtonStyle.primary, discord.ButtonStyle.danger},
        )
        self.assertEqual(len({button.custom_id for button in buttons}), 3)
        self.assertIn("# COIN FLIP", self.view._panel_text.content)
        self.assertIn("**Bet:** 100", self.view._panel_text.content)
        self.assertIn("**Chance:** 50%", self.view._panel_text.content)
        self.assertIn("Bonus: +0.9x", self.view._panel_text.content)

    def test_coinflip_panel_uses_its_own_brand_accent(self):
        container = next(
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Container)
        )
        self.assertEqual(container.accent_color, solo_betting_module.THEME["gold"])

    def test_dice_view_keeps_only_title_and_wager_with_valid_button_rows(self):
        view = SoloWagerView(
            self.cog,
            self.requester,
            100,
            game_type="dice",
            game_title="DICE",
            choices=DICE_CHOICES,
            payout_numerator=570,
            odds_description="16.67%",
            instructions="Pick a number from 1 to 6.",
        )
        try:
            self.assertIn("# DICE", view._panel_text.content)
            self.assertIn("**Bet:** 100", view._panel_text.content)
            self.assertIn("**Chance:** 16.67%", view._panel_text.content)
            self.assertIn("Pick a number from 1 to 6.", view._panel_text.content)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 7)
            self.assertEqual(
                {button.label for button in buttons},
                {"1", "2", "3", "4", "5", "6", "Cancel"},
            )
            self.assertEqual(sum(button.style is discord.ButtonStyle.primary for button in buttons), 6)
            self.assertEqual(sum(button.style is discord.ButtonStyle.danger for button in buttons), 1)
            self.assertEqual(len({button.custom_id for button in buttons}), 7)
            rows = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.ActionRow)
            ]
            self.assertEqual(len(rows), 2)
            self.assertEqual([len(row.children) for row in rows], [3, 4])
        finally:
            view.stop()

    def test_slots_view_keeps_only_title_and_wager_with_one_spin_action(self):
        view = SoloWagerView(
            self.cog,
            self.requester,
            100,
            game_type="slots",
            game_title="SLOTS",
            choices=SLOT_CHOICES,
            payout_numerator=170,
            odds_description="44.44% any win",
            instructions="Spin 3 reels.",
            payout_description="Pair: +0.7x · Trips: +4x–14x bonus",
            outcome_resolver=_spin_slots,
        )
        try:
            content = view._panel_text.content
            self.assertIn("# SLOTS", content)
            self.assertIn("**Bet:** 100", content)
            self.assertIn("**Chance:** 44.44% any win", content)
            self.assertIn("Pair: +0.7x · Trips: +4x–14x bonus", content)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 2)
            self.assertEqual({button.label for button in buttons}, {"Spin", "Cancel"})
            self.assertEqual({button.style for button in buttons}, {discord.ButtonStyle.primary, discord.ButtonStyle.danger})
            self.assertIn("slots_spin", {button.custom_id for button in buttons})
        finally:
            view.stop()

    def test_coinflip_dice_and_slots_panels_include_generated_artwork(self):
        for game_type, title, choices in (
            ("coinflip", "COIN FLIP", SIDES),
            ("dice", "DICE", DICE_CHOICES),
            ("slots", "SLOTS", SLOT_CHOICES),
        ):
            with self.subTest(game_type=game_type):
                view = SoloWagerView(
                    self.cog,
                    self.requester,
                    100,
                    game_type=game_type,
                    game_title=title,
                    choices=choices,
                    outcome_resolver=_spin_slots if game_type == "slots" else None,
                )
                try:
                    self.assertTrue(view._art_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertEqual(view._art_filename, f"{game_type}.png")
                    galleries = [
                        item for item in view.walk_children()
                        if isinstance(item, discord.ui.MediaGallery)
                    ]
                    self.assertEqual(len(galleries), 1)
                finally:
                    view.stop()

    async def test_dice_result_uses_only_current_balance_and_includes_art(self):
        context = SimpleNamespace(interaction=object(), send=AsyncMock())
        view = SoloWagerView(
            self.cog,
            self.requester,
            10,
            game_type="dice",
            game_title="DICE",
            choices=DICE_CHOICES,
            payout_numerator=570,
            context=context,
        )
        view.message = SimpleNamespace(id=777, edit=AsyncMock())
        original_art = view._art_bytes
        try:
            await view._finalize({
                "status": "settled",
                "outcome": "loss",
                "choice": "2",
                "result": "4",
                "stake": 10,
                "payout": 0,
                "balance_after": 53,
            })
            board_text = view._panel_text.content
            self.assertIn("# LOSS · DICE", board_text)
            self.assertIn("Pick: **2** · Result: **4**", board_text)
            self.assertIn("**Balance:** 53", board_text)
            self.assertIn("Bet: 10", board_text)
            self.assertIn("Bonus: +0", board_text)
            self.assertNotEqual(view._art_bytes, original_art)
            view.message.edit.assert_awaited_once()
            edit_kwargs = view.message.edit.await_args.kwargs
            self.assertIs(edit_kwargs["view"], view)
            self.assertEqual(edit_kwargs["attachments"][0].filename, "dice.png")
            context.send.assert_not_awaited()
            self.assertTrue(view.result_published)
        finally:
            view.stop()

    async def test_winning_coinflip_updates_the_original_v2_panel(self):
        self.cog._schedule_recovery = Mock()
        context = SimpleNamespace(interaction=object(), send=AsyncMock())
        self.view.context = context
        message = SimpleNamespace(id=555, edit=AsyncMock())
        interaction = SimpleNamespace(
            user=self.requester,
            channel_id=999,
            message=message,
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
        )
        receipt = {
            "created": True,
            "settled": True,
            "already_settled": False,
            "status": "settled",
            "outcome": "win",
            "choice": "heads",
            "result": "heads",
            "stake": 100,
            "payout": 190,
            "balance_after": 1_090,
        }
        with (
            patch.object(solo_betting_module.secrets, "choice", return_value="heads"),
            patch.object(
                solo_betting_module.bank,
                "play_casino_wager",
                return_value=receipt,
            ) as play_wager,
        ):
            await self.view.choose(interaction, side="heads")

        play_wager.assert_called_once_with(
            self.view.game_id,
            self.requester.id,
            "coinflip",
            100,
            "heads",
            "heads",
            True,
            190,
            channel_id=999,
            message_id=555,
        )
        interaction.response.edit_message.assert_awaited_once_with(view=self.view)
        message.edit.assert_awaited_once()
        edit_kwargs = message.edit.await_args.kwargs
        self.assertIs(edit_kwargs["view"], self.view)
        self.assertEqual(edit_kwargs["attachments"][0].filename, "coinflip.png")
        self.assertTrue(self.view.done)
        self.assertIn("# WIN · COIN FLIP", self.view._panel_text.content)
        self.assertIn("**Balance:** 1,090", self.view._panel_text.content)
        self.assertIn("Bonus: **+90", self.view._panel_text.content)
        self.assertIn("Play Again", {item.label for item in self.view.walk_children() if isinstance(item, discord.ui.Button)})
        context.send.assert_not_awaited()
        self.assertTrue(self.view.result_published)

    async def test_settled_result_updates_the_original_message_without_sending_receipt(self):
        context = SimpleNamespace(interaction=object(), send=AsyncMock())
        self.view.context = context
        self.view.message = SimpleNamespace(id=556, edit=AsyncMock())
        receipt = {
            "status": "settled",
            "outcome": "win",
            "choice": "heads",
            "result": "heads",
            "stake": 100,
            "payout": 190,
            "balance_after": 1_090,
        }

        await self.view._finalize(receipt)

        self.view.message.edit.assert_awaited_once()
        self.assertTrue(self.view.result_published)
        context.send.assert_not_awaited()
        self.assertIn("# WIN · COIN FLIP", self.view._panel_text.content)
        self.assertIn("**Balance:** 1,090", self.view._panel_text.content)
        self.assertIn("Bonus: **+90", self.view._panel_text.content)
        self.assertIn("Play Again", {item.label for item in self.view.walk_children() if isinstance(item, discord.ui.Button)})

    async def test_discord_panel_update_failure_releases_session_without_betting(self):
        self.view.message = SimpleNamespace(id=702, edit=AsyncMock())
        interaction = SimpleNamespace(
            user=self.requester,
            channel_id=999,
            message=self.view.message,
            response=SimpleNamespace(
                edit_message=AsyncMock(
                    side_effect=discord.HTTPException(
                        SimpleNamespace(status=500, reason="simulated failure"),
                        "simulated failure",
                    )
                ),
                send_message=AsyncMock(),
            ),
        )
        with patch.object(solo_betting_module.bank, "play_casino_wager") as play_wager:
            await self.view.choose(interaction, side="heads")

        play_wager.assert_not_called()
        self.assertTrue(self.view.done)
        self.assertFalse(self.view.resolving)
        self.assertTrue(all(button.disabled for button in self.view._buttons.values()))
        self.assertIn("# Could not start", self.view._panel_text.content)
        self.view.message.edit.assert_awaited_once()
        edit_kwargs = self.view.message.edit.await_args.kwargs
        self.assertIs(edit_kwargs["view"], self.view)
        self.assertEqual(edit_kwargs["attachments"][0].filename, "coinflip.png")

    async def test_cancel_before_choice_never_calls_the_wager_service(self):
        interaction = SimpleNamespace(
            user=self.requester,
            message=SimpleNamespace(id=700),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        with patch.object(solo_betting_module.bank, "play_casino_wager") as play_wager:
            await self.view.cancel(interaction)

        play_wager.assert_not_called()
        interaction.response.edit_message.assert_awaited_once_with(view=self.view)
        self.assertTrue(self.view.done)
        self.assertIn("# CANCELLED · COIN FLIP", self.view._panel_text.content)
        self.assertIn("No bet was placed.", self.view._panel_text.content)

    async def test_cancelled_panel_edit_still_releases_claimed_solo_session(self):
        self.assertTrue(claim_solo_session(self.requester.id, self.view))
        self.assertTrue(has_active_solo_session(self.requester.id))
        with (
            patch.object(
                self.view,
                "_edit_message",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            self.assertRaises(asyncio.CancelledError),
        ):
            await self.view._finish_without_bet("# تم إلغاء الجولة")

        self.assertFalse(has_active_solo_session(self.requester.id))
        self.assertTrue(self.view.done)

    async def test_cancel_api_failure_still_releases_session_without_a_bet(self):
        self.assertTrue(claim_solo_session(self.requester.id, self.view))
        self.assertTrue(has_active_solo_session(self.requester.id))
        self.view.message = SimpleNamespace(id=703, edit=AsyncMock())
        interaction = SimpleNamespace(
            user=self.requester,
            message=self.view.message,
            response=SimpleNamespace(
                edit_message=AsyncMock(
                    side_effect=discord.HTTPException(
                        SimpleNamespace(status=500, reason="simulated failure"),
                        "simulated failure",
                    )
                )
            ),
        )
        with patch.object(solo_betting_module.bank, "play_casino_wager") as play_wager:
            await self.view.cancel(interaction)

        play_wager.assert_not_called()
        self.view.message.edit.assert_awaited_once()
        edit_kwargs = self.view.message.edit.await_args.kwargs
        self.assertIs(edit_kwargs["view"], self.view)
        self.assertEqual(edit_kwargs["attachments"][0].filename, "coinflip.png")
        self.assertFalse(has_active_solo_session(self.requester.id))
        self.assertTrue(self.view.done)

    async def test_timeout_before_choice_never_calls_the_wager_service(self):
        self.view.message = SimpleNamespace(id=701, edit=AsyncMock())
        with patch.object(solo_betting_module.bank, "play_casino_wager") as play_wager:
            await self.view.on_timeout()

        play_wager.assert_not_called()
        self.view.message.edit.assert_awaited_once()
        edit_kwargs = self.view.message.edit.await_args.kwargs
        self.assertIs(edit_kwargs["view"], self.view)
        self.assertEqual(edit_kwargs["attachments"][0].filename, "coinflip.png")
        self.assertTrue(self.view.done)
        self.assertIn("# EXPIRED · COIN FLIP", self.view._panel_text.content)

    async def test_session_is_bound_to_the_requesting_member(self):
        other_user = SimpleNamespace(id=202)
        interaction = SimpleNamespace(
            user=other_user,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        allowed = await self.view.interaction_check(interaction)

        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], StatusPanel)

    async def test_session_owner_can_pass_interaction_check(self):
        interaction = SimpleNamespace(
            user=self.requester,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertTrue(await self.view.interaction_check(interaction))
        interaction.response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
