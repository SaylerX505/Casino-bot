"""Regression coverage for the riddle game and shared Components V2 status style."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, mock_open, patch

import discord
from discord.ui import LayoutView

from cogs.casino.currency_views import CurrencyHubLayout, ShopView, profile_panel
from cogs.casino.fazoora import DEFAULT_QUESTIONS, load_bank, normalize
from cogs.utility.theme import (
    StatusPanel,
    base_embed,
    embed_to_v2,
    send_status,
)


class RiddleInputTests(unittest.TestCase):
    def test_arabic_answer_matching_ignores_tashkeel_punctuation_and_extra_spacing(self):
        self.assertEqual(normalize("  الحُفْرَة؟   "), normalize("الحفرة"))
        self.assertEqual(normalize("إجابةٌ صحيحة!"), normalize("اجابه صحيحه"))

    def test_empty_bank_falls_back_to_a_usable_question(self):
        with patch("builtins.open", mock_open(read_data=json.dumps({"فزورة": []}, ensure_ascii=False))):
            self.assertEqual(load_bank(), DEFAULT_QUESTIONS)

    def test_malformed_rows_are_filtered_without_disabling_the_game(self):
        payload = {
            "فزورة": [
                None,
                {"q": "", "a": ["answer"]},
                {"q": "سؤال غير صالح", "a": []},
                {"q": "سؤال صالح؟", "a": ["إجابة!"]},
            ]
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(payload, ensure_ascii=False))):
            self.assertEqual(load_bank(), [{"q": "سؤال صالح؟", "a": ["إجابة!"]}])


class EconomyHubUXTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=101, display_name="Test member")
        self.cog = SimpleNamespace(
            get_balance=lambda user_id: 250,
            get_inventory=lambda user_id: {"loot_box": 1},
        )

    def test_hub_has_direct_inventory_and_profile_actions(self):
        view = CurrencyHubLayout(self.cog, self.user)
        self.assertIsInstance(view, LayoutView)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        custom_ids = {button.custom_id for button in buttons}
        self.assertTrue({"hubv2_inventory", "hubv2_profile"}.issubset(custom_ids))
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))

    def test_command_and_hub_can_share_profile_panel_data(self):
        with patch("cogs.casino.currency_views.bank.get_daily", return_value=(0, 4)):
            panel = profile_panel(self.cog, self.user)
        self.assertIsInstance(panel, LayoutView)
        content = "\n".join(
            item.content
            for item in panel.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("الرصيد", content)
        self.assertIn("4 يوم", content)
        self.assertIn("الأغراض", content)


    def test_shop_shows_the_requesters_current_balance(self):
        view = ShopView(self.cog, self.user)
        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("رصيدك الحالي", content)
        self.assertIn("250", content)


class ShopTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_shop_disables_controls_and_edits_the_message(self):
        user = SimpleNamespace(id=7)
        cog = SimpleNamespace(get_balance=lambda _user_id: 50)
        view = ShopView(cog, user)
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        controls = [item for item in view.walk_children() if hasattr(item, "disabled")]
        self.assertTrue(controls)
        self.assertTrue(all(item.disabled for item in controls))
        view.message.edit.assert_awaited_once_with(view=view)


class SharedStatusPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_status_is_visible_and_uses_components_v2(self):
        ctx = SimpleNamespace(interaction=None, send=AsyncMock())

        await send_status(ctx, "الروم مشغول", "فيه لعبة شغالة.", "warning")

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertNotIn("ephemeral", kwargs)

    async def test_slash_status_is_ephemeral_and_uses_components_v2(self):
        ctx = SimpleNamespace(interaction=object(), send=AsyncMock())

        await send_status(ctx, "الروم مشغول", "فيه لعبة شغالة.", "warning")

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertTrue(kwargs["ephemeral"])

    def test_base_panels_have_clean_footer_and_v2_conversion(self):
        legacy = base_embed(title="اختبار", description="محتوى")
        self.assertNotIn("Developed by", legacy.footer.text)
        self.assertIsInstance(embed_to_v2(legacy), LayoutView)


if __name__ == "__main__":
    unittest.main()
