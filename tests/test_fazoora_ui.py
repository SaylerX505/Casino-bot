"""Regression coverage for the riddle game and shared Components V2 status style."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, mock_open, patch

from discord.ui import LayoutView

from cogs.casino.fazoora import DEFAULT_QUESTIONS, load_bank, normalize
from cogs.utility.theme import StatusPanel, WatermarkView, base_embed, embed_to_v2, send_status



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

    def test_base_panels_have_no_promotional_button_or_footer_noise(self):
        self.assertEqual(len(WatermarkView().children), 0)
        legacy = base_embed(title="اختبار", description="محتوى")
        self.assertNotIn("Developed by", legacy.footer.text)
        self.assertIsInstance(embed_to_v2(legacy), LayoutView)


if __name__ == "__main__":
    unittest.main()
