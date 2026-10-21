"""Regression tests for the musical-chairs Components V2 lobby."""
import asyncio
import contextlib
import unittest
from types import SimpleNamespace

import discord
from discord.ui import LayoutView

from cogs.casino.chairs import ChairsLobby
from cogs.casino.geoguessr import GuessGameMenu
from cogs.casino.hide_and_seek import LobbyView
from cogs.casino.roulette import RouletteLobby
from cogs.casino.mafia import MafiaLobbyView
from cogs.casino.wheel import WheelLobby


class _FakeMember:
    def __init__(self, user_id):
        self.id = user_id
        self.mention = f"<@{user_id}>"

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id


class MusicalChairsV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_lobby_uses_v2_layout_and_secondary_controls(self):
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop),
            channel=SimpleNamespace(id=100),
            author=SimpleNamespace(id=200),
        )
        view = ChairsLobby(ctx)
        try:
            self.assertIsInstance(view, LayoutView)
            buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
            self.assertEqual(len(buttons), 4)
            self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
            self.assertTrue(all(button.custom_id.startswith("chairs_") for button in buttons))
            self.assertIn("طريقة اللعب", view._panel_text.content)
            view.time_left += 10
            view._refresh_panel()
            self.assertIn("45", view._panel_text.content)
            view._disable_buttons()
            self.assertTrue(all(button.disabled for button in buttons))
        finally:
            task = getattr(view, "timer_task", None) or getattr(view, "lobby_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


class OtherGameLobbyV2Tests(unittest.IsolatedAsyncioTestCase):
    async def _assert_v2_lobby(self, view_type, expected_ids, *, player_collection="list"):
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop),
            channel=SimpleNamespace(id=100),
            author=SimpleNamespace(id=200),
        )
        view = view_type(ctx)
        try:
            self.assertIsInstance(view, LayoutView)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 4)
            self.assertTrue(
                all(button.style is discord.ButtonStyle.secondary for button in buttons)
            )
            self.assertEqual(
                {button.custom_id for button in buttons},
                set(expected_ids),
            )
            player = _FakeMember(303)
            view.players.add(player) if player_collection == "set" else view.players.append(player)
            view._refresh_panel()
            self.assertIn(player.mention, view._panel_text.content)
            self.assertNotIn(chr(92) + "n", view._panel_text.content)
        finally:
            view.timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await view.timer_task

    async def test_geoguessr_lobby_uses_v2_components(self):
        await self._assert_v2_lobby(
            GuessGameMenu,
            {"geoguessr_join", "geoguessr_leave", "geoguessr_start", "geoguessr_add_time"},
            player_collection="set",
        )

    async def test_hide_and_seek_lobby_uses_v2_components(self):
        await self._assert_v2_lobby(
            LobbyView,
            {"hide_seek_join", "hide_seek_leave", "hide_seek_start", "hide_seek_add_time"},
        )

    async def test_roulette_lobby_uses_v2_components(self):
        await self._assert_v2_lobby(
            RouletteLobby,
            {"roulette_join", "roulette_leave", "roulette_start", "roulette_add_time"},
        )

    async def test_wheel_lobby_uses_v2_components(self):
        await self._assert_v2_lobby(
            WheelLobby,
            {"wheel_join", "wheel_leave", "wheel_start"},
        )

    async def test_mafia_lobby_uses_v2_components_and_keeps_cover_gallery(self):
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop),
            channel=SimpleNamespace(id=100),
            author=SimpleNamespace(id=200),
        )
        view = MafiaLobbyView(ctx)
        try:
            self.assertIsInstance(view, LayoutView)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 4)
            self.assertTrue(
                all(button.style is discord.ButtonStyle.secondary for button in buttons)
            )
            galleries = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.MediaGallery)
            ]
            self.assertEqual(len(galleries), 1)
            self.assertIn("attachment://menu.jpg", str(galleries[0].items[0].media.url))
        finally:
            task = view.timer_task
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
