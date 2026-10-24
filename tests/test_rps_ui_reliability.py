"""Regression tests for RPS panel updates that must not duplicate live challenges."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
