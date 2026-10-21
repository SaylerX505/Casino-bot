"""Regression tests for the musical-chairs Components V2 lobby."""
import asyncio
import contextlib
import unittest
from types import SimpleNamespace

import discord
from discord.ui import LayoutView

from cogs.casino.chairs import ChairActionView, ChairPrepView, ChairsLobby
from cogs.casino.geoguessr import (
    ActiveGameView,
    GeoGuessrResultPanel,
    GuessGameMenu,
)
from cogs.casino.hide_and_seek import HidingView, LobbyView, SeekerView
from cogs.casino.mafia import (
    DayVoteView,
    EphemeralActionView,
    MafiaLobbyView,
    SecretButtonView,
)
from cogs.casino.roulette import KickView, RouletteLobby
from cogs.casino.wheel import WheelLobby, WheelResultPanel
from cogs.utility.theme import embed_to_v2


class _FakeMember:
    def __init__(self, user_id):
        self.id = user_id
        self.mention = f"<@{user_id}>"
        self.display_name = f"user-{user_id}"

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id


class LegacyAnnouncementMigrationTests(unittest.TestCase):
    def test_legacy_embed_renderer_keeps_text_and_attached_art_in_v2(self):
        legacy = discord.Embed(
            title="اختبار نتيجة",
            description="النتيجة ظهرت بشكل منفصل.",
            color=discord.Color.orange(),
        )
        legacy.add_field(name="التفاصيل", value="اللاعب كسب 20 نقطة.", inline=False)
        legacy.set_footer(text="Çukur • Game Result")
        legacy.set_image(url="attachment://result.png")

        panel = embed_to_v2(legacy)
        self.assertIsInstance(panel, LayoutView)
        text = "\\n".join(
            item.content
            for item in panel.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("اختبار نتيجة", text)
        self.assertIn("النتيجة ظهرت بشكل منفصل", text)
        self.assertIn("اللاعب كسب 20 نقطة", text)
        self.assertIn("Çukur • Game Result", text)
        gallery = next(
            item for item in panel.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        )
        self.assertIn("attachment://result.png", str(gallery.items[0].media.url))


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


class GeoGuessrRoundV2Tests(unittest.TestCase):
    def test_active_round_panel_keeps_image_timer_and_secondary_action(self):
        executor = _FakeMember(6101)
        timer_state = {"end_time": 9999999999.0}
        view = ActiveGameView(executor, timer_state, round_num=2, total_rounds=6)
        self.assertIsInstance(view, LayoutView)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        galleries = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        ]
        self.assertEqual(len(buttons), 1)
        self.assertIs(buttons[0].style, discord.ButtonStyle.secondary)
        self.assertEqual(len(galleries), 1)
        self.assertIn("الجولة 2 من 6", view._panel_text.content)
        self.assertIn("attachment://streetview.png", str(galleries[0].items[0].media.url))
        view.update_timer(17)
        self.assertIn("17", view._panel_text.content)
        view.disable_controls()
        self.assertTrue(buttons[0].disabled)
        view.stop()

    def test_geoguessr_result_panel_uses_v2_image_gallery(self):
        panel = GeoGuessrResultPanel(
            "خريطة النتائج",
            "أماكن تخمينات اللاعبين.",
            outcome="neutral",
            image_name="result_map.png",
        )
        self.assertIsInstance(panel, LayoutView)
        gallery = next(
            item for item in panel.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        )
        self.assertIn("attachment://result_map.png", str(gallery.items[0].media.url))


class MafiaSecretActionV2Tests(unittest.TestCase):
    def test_ephemeral_target_picker_is_v2_and_private_ready(self):
        targets = [_FakeMember(5101), _FakeMember(5102)]
        view = EphemeralActionView(targets)
        self.assertIsInstance(view, LayoutView)
        selects = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        self.assertEqual(len(selects), 1)
        self.assertIn("اختيار سري", view._panel_text.content)
        self.assertEqual(len(selects[0].options), 2)
        view.disable_controls()
        self.assertTrue(selects[0].disabled)
        view.stop()

    def test_secret_action_panel_keeps_role_image_and_secondary_button(self):
        active_user = _FakeMember(5201)
        role_users = [active_user, _FakeMember(5202)]
        targets = [_FakeMember(5203), _FakeMember(5204)]
        for role_name, image_name in (
            ("mafia", "mafia_looking.jpg"),
            ("doctor", "doctor_looking.jpg"),
        ):
            view = SecretButtonView(active_user, role_users, targets, role_name)
            self.assertIsInstance(view, LayoutView)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            galleries = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.MediaGallery)
            ]
            self.assertEqual(len(buttons), 1)
            self.assertIs(buttons[0].style, discord.ButtonStyle.secondary)
            self.assertEqual(len(galleries), 1)
            self.assertIn(image_name, str(galleries[0].items[0].media.url))
            view.disable_controls()
            self.assertTrue(buttons[0].disabled)
            view.stop()


class MafiaVoteV2Tests(unittest.TestCase):
    def test_day_vote_panel_uses_v2_select_and_secondary_skip(self):
        players = [_FakeMember(4100 + index) for index in range(5)]
        view = DayVoteView(players)
        self.assertIsInstance(view, LayoutView)
        selects = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual(len(buttons), 1)
        self.assertIs(buttons[0].style, discord.ButtonStyle.secondary)
        self.assertEqual(selects[0].max_values, 1)
        self.assertIn("في انتظار تصويت المواطنين", view._panel_text.content)

        view.votes[players[0]] = players[1].id
        view.votes[players[2]] = "SKIP"
        view._panel_text.content = view._vote_summary()
        self.assertIn(players[0].mention, view._panel_text.content)
        self.assertIn(players[1].mention, view._panel_text.content)
        self.assertIn("تفويت", view._panel_text.content)

        view.disable_controls()
        self.assertTrue(selects[0].disabled)
        self.assertTrue(buttons[0].disabled)
        view.stop()


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


class MusicalChairsRoundV2Tests(unittest.TestCase):
    def test_prep_and_action_rounds_use_secondary_v2_button_rows(self):
        members = [_FakeMember(2000 + index) for index in range(25)]
        prep = ChairPrepView(round_num=2, num_chairs=24)
        action = ChairActionView(members, num_chairs=24, round_type="normal", round_num=2)
        try:
            self.assertIsInstance(prep, LayoutView)
            self.assertIsInstance(action, LayoutView)
            prep_buttons = [
                item for item in prep.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            action_buttons = [
                item for item in action.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(prep_buttons), 24)
            self.assertEqual(len(action_buttons), 24)
            self.assertTrue(all(button.disabled for button in prep_buttons))
            self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in action_buttons))
            action.disable_controls()
            self.assertTrue(all(button.disabled for button in action_buttons))
        finally:
            prep.stop()
            action.stop()

    def test_broken_chair_round_marks_disabled_controls_and_eliminations(self):
        members = [_FakeMember(3000 + index) for index in range(4)]
        action = ChairActionView(members, num_chairs=3, round_type="forbidden", round_num=3)
        try:
            self.assertIsInstance(action, LayoutView)
            buttons = [
                item for item in action.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 3)
            self.assertIn("فخ", action._panel_text.content)
            action.eliminated_fools.add(members[0])
            action.disable_controls()
            self.assertTrue(all(button.disabled for button in buttons))
        finally:
            action.stop()


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

    def test_wheel_result_panel_uses_v2_gallery_when_result_has_art(self):
        panel = WheelResultPanel(
            "نتيجة اللفة",
            "النتيجة: x2\nصافي الرصيد: +50",
            outcome="win",
            image_name="wheel.png",
        )
        self.assertIsInstance(panel, LayoutView)
        galleries = [
            item for item in panel.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        ]
        self.assertEqual(len(galleries), 1)
        self.assertIn("attachment://wheel.png", str(galleries[0].items[0].media.url))
        self.assertIn("صافي الرصيد", panel._panel_text.content)

    def test_wheel_final_result_uses_v2_without_gallery(self):
        panel = WheelResultPanel("انتهت العجلة", "مفيش نتائج.", outcome="success")
        self.assertIsInstance(panel, LayoutView)
        self.assertFalse(
            any(isinstance(item, discord.ui.MediaGallery) for item in panel.walk_children())
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
