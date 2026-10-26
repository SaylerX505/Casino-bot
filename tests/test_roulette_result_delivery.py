"""Receipt-delivery regression tests for roulette round and final results."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.casino.roulette import RouletteLobby
from cogs.utility.theme import StatusPanel


class RouletteReceiptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def make_lobby(self, send):
        return SimpleNamespace(
            ctx=SimpleNamespace(
                channel=SimpleNamespace(id=303),
                send=send,
            )
        )

    async def test_success_keeps_round_receipt_as_a_separate_message(self):
        send = AsyncMock()
        lobby = self.make_lobby(send)
        message = SimpleNamespace(edit=AsyncMock())
        view = SimpleNamespace(_panel_text=SimpleNamespace(content="Closed round placeholder"))

        published = await RouletteLobby._publish_result_receipt(
            lobby,
            message,
            view,
            title="نتيجة الجولة 2",
            description="اللاعب خرج من اللعبة.",
            outcome="warning",
            context="round_2",
        )

        self.assertTrue(published)
        send.assert_awaited_once()
        self.assertIsInstance(send.await_args.kwargs["view"], StatusPanel)
        message.edit.assert_not_awaited()
        self.assertEqual(view._panel_text.content, "Closed round placeholder")

    async def test_send_failure_restores_full_details_on_the_closed_round_board(self):
        send = AsyncMock(side_effect=RuntimeError("synthetic receipt failure"))
        lobby = self.make_lobby(send)
        message = SimpleNamespace(edit=AsyncMock())
        view = SimpleNamespace(_panel_text=SimpleNamespace(content="Closed round placeholder"))

        published = await RouletteLobby._publish_result_receipt(
            lobby,
            message,
            view,
            title="نتيجة الجولة 2",
            description="تم طرد اللاعب بعد تفعيل الدرع.",
            outcome="warning",
            context="round_2",
        )

        self.assertFalse(published)
        send.assert_awaited_once()
        message.edit.assert_awaited_once_with(view=view)
        self.assertIn("نتيجة الجولة 2", view._panel_text.content)
        self.assertIn("تم طرد اللاعب بعد تفعيل الدرع.", view._panel_text.content)


if __name__ == "__main__":
    unittest.main()
