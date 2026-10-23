"""Lobby startup failures must be logged privately, not echoed to Discord."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.casino.hide_and_seek import HideAndSeek
from cogs.casino.mafia import Mafia
from cogs.utility.theme import StatusPanel


def panel_text(view):
    return "\n".join(
        child.content
        for child in view.walk_children()
        if isinstance(child, discord.ui.TextDisplay)
    )


def fake_lobby():
    return SimpleNamespace(
        timer_task=SimpleNamespace(cancel=Mock()),
        message=None,
        _disable_buttons=Mock(),
    )


class LobbyStartupErrorSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_hide_and_seek_startup_failure_does_not_echo_exception(self):
        bot = SimpleNamespace()
        lobby = fake_lobby()
        ctx = SimpleNamespace(
            bot=bot,
            channel=SimpleNamespace(id=303),
            author=SimpleNamespace(id=101),
            interaction=None,
            send=AsyncMock(
                side_effect=[
                    RuntimeError("secret internal path /app/private/runtime"),
                    None,
                ]
            ),
        )

        with (
            patch("cogs.casino.hide_and_seek.is_active_game", return_value=False),
            patch("cogs.casino.hide_and_seek.LobbyView", return_value=lobby),
            patch("cogs.casino.hide_and_seek.register_game_task", return_value=True),
            patch("cogs.casino.hide_and_seek.release_game_task") as release,
            patch("cogs.casino.hide_and_seek.logger.exception") as log_exception,
        ):
            await HideAndSeek.start_hide_seek.callback(HideAndSeek(bot), ctx)

        lobby.timer_task.cancel.assert_called_once()
        release.assert_called_once_with(bot, 303, lobby.timer_task)
        log_exception.assert_called_once()
        self.assertEqual(ctx.send.await_count, 2)
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        rendered = panel_text(kwargs["view"])
        self.assertIn("تعذر فتح لوبي الغميضة", rendered)
        self.assertNotIn("secret internal path", rendered)
        self.assertNotIn("/app/private/runtime", rendered)
        self.assertNotIn("ephemeral", kwargs)

    async def test_mafia_startup_failure_is_private_for_slash_and_redacted(self):
        bot = SimpleNamespace()
        lobby = fake_lobby()
        ctx = SimpleNamespace(
            bot=bot,
            channel=SimpleNamespace(id=404),
            author=SimpleNamespace(id=202),
            interaction=SimpleNamespace(),
            send=AsyncMock(
                side_effect=[
                    RuntimeError("token=private-secret-value"),
                    None,
                ]
            ),
        )

        with (
            patch("cogs.casino.mafia.discord.File", return_value=object()),
            patch("cogs.casino.mafia.is_active_game", return_value=False),
            patch("cogs.casino.mafia.MafiaLobbyView", return_value=lobby),
            patch("cogs.casino.mafia.register_game_task", return_value=True),
            patch("cogs.casino.mafia.release_game_task") as release,
            patch("cogs.casino.mafia.logger.exception") as log_exception,
        ):
            await Mafia.start_mafia.callback(Mafia(bot), ctx)

        lobby.timer_task.cancel.assert_called_once()
        release.assert_called_once_with(bot, 404, lobby.timer_task)
        log_exception.assert_called_once()
        self.assertEqual(ctx.send.await_count, 2)
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertTrue(kwargs["ephemeral"])
        rendered = panel_text(kwargs["view"])
        self.assertIn("تعذر فتح لوبي المافيا", rendered)
        self.assertNotIn("private-secret-value", rendered)
        self.assertNotIn("token=", rendered)


if __name__ == "__main__":
    unittest.main()
