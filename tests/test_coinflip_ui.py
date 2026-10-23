"""UI regression tests for the single-player coinflip session."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ui import LayoutView

from cogs.casino import solo_betting as solo_betting_module
from cogs.casino.solo_betting import (
    DICE_CHOICES,
    SLOT_CHOICES,
    SoloWagerView,
    _spin_slots,
)
from cogs.utility.theme import StatusPanel


class SoloWagerViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requester = SimpleNamespace(id=101, mention="<@101>")
        self.cog = SimpleNamespace()
        self.view = SoloWagerView(self.cog, self.requester, 100)

    async def asyncTearDown(self):
        self.view.stop()

    def test_coinflip_uses_v2_layout_and_secondary_buttons(self):
        self.assertIsInstance(self.view, LayoutView)
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
        self.assertEqual({button.label for button in buttons}, {"صورة", "كتابة", "إلغاء"})
        self.assertEqual(len({button.custom_id for button in buttons}), 3)
        self.assertIn("1.9x", self.view._panel_text.content)
        self.assertIn("100", self.view._panel_text.content)

    def test_dice_view_discloses_odds_and_uses_two_valid_button_rows(self):
        view = SoloWagerView(
            self.cog,
            self.requester,
            100,
            game_type="dice",
            game_title="رمي النرد",
            choices=DICE_CHOICES,
            payout_numerator=570,
            odds_description="1 من 6",
            instructions="اختار رقم من 1 إلى 6.",
        )
        try:
            self.assertIn("5.7x", view._panel_text.content)
            self.assertIn("1 من 6", view._panel_text.content)
            self.assertIn("570", view._panel_text.content)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 7)
            self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
            self.assertEqual(
                {button.label for button in buttons},
                {"1", "2", "3", "4", "5", "6", "إلغاء"},
            )
            self.assertEqual(len({button.custom_id for button in buttons}), 7)
            rows = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.ActionRow)
            ]
            self.assertEqual(len(rows), 2)
            self.assertEqual([len(row.children) for row in rows], [3, 4])
        finally:
            view.stop()

    def test_slots_view_discloses_variable_paytable_with_one_spin_action(self):
        view = SoloWagerView(
            self.cog,
            self.requester,
            100,
            game_type="slots",
            game_title="ماكينة الحظ",
            choices=SLOT_CHOICES,
            payout_numerator=170,
            odds_description="الرموز مستقلة ومتساوية الاحتمال",
            instructions="اضغط تدوير لتجربة 3 بكرات.",
            payout_description="زوج متطابق: 1.7x · ثلاثية: 5x-15x",
            outcome_resolver=_spin_slots,
        )
        try:
            content = view._panel_text.content
            self.assertIn("جدول الجوائز", content)
            self.assertIn("1.7x", content)
            self.assertIn("15x", content)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 2)
            self.assertEqual({button.label for button in buttons}, {"تدوير", "إلغاء"})
            self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
            self.assertIn("slots_spin", {button.custom_id for button in buttons})
        finally:
            view.stop()

    async def test_winning_coinflip_edits_the_original_message_with_atomic_receipt(self):
        self.cog._schedule_recovery = Mock()
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
        message.edit.assert_awaited_once_with(view=self.view)
        self.assertTrue(self.view.done)
        self.assertIn("كسبت الرهان", self.view._panel_text.content)
        self.assertIn("صافي الربح:** +90", self.view._panel_text.content)
        self.assertIn("رصيدك بعد التسوية:** 1,090", self.view._panel_text.content)

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
