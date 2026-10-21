"""Regression tests for the musical-chairs Components V2 lobby."""
import asyncio
import contextlib
import unittest
from types import SimpleNamespace

import discord
from discord.ui import LayoutView

from cogs.casino.chairs import ChairsLobby
from cogs.casino.geoguessr import GuessGameMenu
from cogs.casino.hide_and_seek import HidingView, LobbyView, SeekerView
from cogs.casino.mafia import MafiaLobbyView
from cogs.casino.roulette import KickView, RouletteLobby
from cogs.casino.wheel import WheelLobby


class _FakeMember:
    def __init__(self, user_id):
        self.id = user_id
        self.mention = f"<@{user_id}>"
        self.display_name = f"user-{user_id}"

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


class HideAndSeekRoundV2Tests(unittest.TestCase):
    def test_hiding_panel_preserves_map_gallery_and_secret_selection(self):
        view = HidingView([_FakeMember(101)])
        self.assertIsInstance(view, LayoutView)
        dropdowns = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        galleries = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        ]
        self.assertEqual(len(dropdowns), 1)
        self.assertEqual(len(galleries), 1)
        self.assertIn("attachment://map.png", str(galleries[0].items[0].media.url))
        view.disable_controls()
        self.assertTrue(dropdowns[0].disabled)

    def test_seeker_panel_is_v2_and_has_only_a_select_action(self):
        view = SeekerView(_FakeMember(202), set())
        self.assertIsInstance(view, LayoutView)
        dropdowns = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        self.assertEqual(len(dropdowns), 1)
        self.assertIn("<@202>", view._panel_text.content)
        view.disable_controls()
        self.assertTrue(dropdowns[0].disabled)


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
            self.assertEqual(len(buttons), len(expected_ids))
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
            task = getattr(view, "timer_task", None) or getattr(view, "lobby_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

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

    def test_roulette_targeting_view_preserves_image_and_safe_control_rows(self):
        members = [_FakeMember(1000 + index) for index in range(31)]
        view = KickView(
            members[0],
            members,
            has_weapon=True,
            currency_cog=SimpleNamespace(),
            round_num=4,
        )

        self.assertIsInstance(view, LayoutView)
        self.assertIn("الجولة 4", view._panel_text.content)
        self.assertIn(members[0].mention, view._panel_text.content)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        selects = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        galleries = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        ]
        self.assertEqual(len(buttons), 1)
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
        self.assertEqual(len(selects), 2)
        self.assertEqual(len(galleries), 1)
        self.assertIn("attachment://sniper.png", str(galleries[0].items[0].media.url))

        view.disable_controls()
        self.assertTrue(buttons[0].disabled)
        self.assertTrue(all(select.disabled for select in selects))

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
