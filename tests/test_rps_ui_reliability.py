"""Regression tests for RPS panel updates that must not duplicate live challenges."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.casino.rps import RPSView


class _Member:
    def __init__(self, user_id):
        self.id = user_id
        self.mention = f"<@{user_id}>"


def _http_error(status, message):
    response = SimpleNamespace(status=status, reason="synthetic test response")
    if status == 404:
        return discord.NotFound(response, message)
    if status == 403:
        return discord.Forbidden(response, message)
    return discord.HTTPException(response, message)


class RPSPanelReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def _view(self):
        player = _Member(101)
        opponent = _Member(202)
        ctx = SimpleNamespace(
            author=player,
            channel=SimpleNamespace(id=303),
            send=AsyncMock(return_value=SimpleNamespace(id=505)),
        )
        view = RPSView(ctx, player, opponent, 100, "rps-panel-test")
        view._store_panel_reference = AsyncMock()
        return view, ctx

    async def test_transient_edit_failure_retries_original_message_without_duplicate(self):
        view, ctx = self._view()
        original = SimpleNamespace(
            id=505,
            edit=AsyncMock(side_effect=[_http_error(503, "upstream unavailable"), None]),
        )
        view.message = original

        updated = await view._update_live_panel()

        self.assertTrue(updated)
        self.assertIs(view.message, original)
        self.assertEqual(original.edit.await_count, 2)
        ctx.send.assert_not_awaited()
        view._store_panel_reference.assert_awaited_once()
        view.stop()

    async def test_permission_edit_failure_keeps_original_and_never_posts_duplicate(self):
        view, ctx = self._view()
        original = SimpleNamespace(
            id=505,
            edit=AsyncMock(side_effect=_http_error(403, "missing permissions")),
        )
        view.message = original

        updated = await view._update_live_panel()

        self.assertFalse(updated)
        self.assertIs(view.message, original)
        original.edit.assert_awaited_once()
        ctx.send.assert_not_awaited()
        view._store_panel_reference.assert_not_awaited()
        view.stop()

    async def test_successful_result_keeps_receipt_separate_and_compacts_closed_board(self):
        view, ctx = self._view()
        message = SimpleNamespace(id=505, edit=AsyncMock())
        view.message = message
        result = discord.Embed(
            title="كسبت الرهان",
            description="**الرهان:** 100\n**صافي الربح:** +90\n**رصيدك بعد التسوية:** 1,090",
        )

        await view.publish_result(result)

        ctx.send.assert_awaited_once()
        self.assertTrue(view.result_published)
        self.assertEqual(message.edit.await_count, 2)
        self.assertIn("إيصال منفصل", view._panel_text.content)
        self.assertNotIn("صافي الربح", view._panel_text.content)
        result_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(result_view, discord.ui.LayoutView)
        result_text = "\n".join(
            item.content for item in result_view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("صافي الربح", result_text)
        view.stop()

    async def test_failed_result_receipt_keeps_full_result_on_closed_board(self):
        view, ctx = self._view()
        ctx.send.side_effect = RuntimeError("temporary result-message failure")
        message = SimpleNamespace(id=505, edit=AsyncMock())
        view.message = message
        result = discord.Embed(
            title="كسبت الرهان",
            description="**الرهان:** 100\n**صافي الربح:** +90\n**رصيدك بعد التسوية:** 1,090",
        )

        await view.publish_result(result)

        ctx.send.assert_awaited_once()
        self.assertFalse(view.result_published)
        message.edit.assert_awaited_once_with(view=view)
        self.assertIn("صافي الربح", view._panel_text.content)
        self.assertIn("1,090", view._panel_text.content)
        self.assertNotIn("إيصال العام التالي", view._panel_text.content)
        self.assertEqual(view._buttons, [])
        view.stop()

    async def test_deferred_player_ack_never_claims_receipt_exists_after_send_failure(self):
        view, ctx = self._view()
        ctx.send.side_effect = RuntimeError("temporary result-message failure")
        message = SimpleNamespace(id=505, edit=AsyncMock())
        view.message = message
        interaction = SimpleNamespace(
            response=SimpleNamespace(is_done=Mock(return_value=True)),
            edit_original_response=AsyncMock(),
        )
        result = discord.Embed(
            title="انتهى التحدي",
            description="**الرهان:** 100\n**صافي النتيجة:** −100",
        )

        await view.publish_result(result, interaction=interaction)

        ack_view = interaction.edit_original_response.await_args.kwargs["view"]
        ack_text = "\n".join(
            item.content for item in ack_view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertFalse(view.result_published)
        self.assertIn("تفاصيل النتيجة والرهان موجودة على لوحة التحدي", ack_text)
        self.assertNotIn("إيصال النتيجة النهائية موجود", ack_text)
        view.stop()

    async def test_replacement_is_sent_only_after_discord_confirms_original_missing(self):
        view, ctx = self._view()
        original = SimpleNamespace(
            id=505,
            edit=AsyncMock(side_effect=_http_error(404, "message deleted")),
        )
        replacement = SimpleNamespace(id=606)
        ctx.send.return_value = replacement
        view.message = original

        updated = await view._update_live_panel()

        self.assertTrue(updated)
        self.assertIs(view.message, replacement)
        original.edit.assert_awaited_once()
        ctx.send.assert_awaited_once()
        view._store_panel_reference.assert_awaited_once()
        view.stop()


if __name__ == "__main__":
    unittest.main()
