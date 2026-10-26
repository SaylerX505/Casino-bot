"""Regression tests for the musical-chairs Components V2 lobby."""
import asyncio
import contextlib
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ui import LayoutView

from cogs.casino.chairs import ChairActionView, ChairPrepView, ChairsLobby
from cogs.casino.geoguessr_views import (
    ActiveGameView,
    GeoGuessrResultPanel,
    GuessGameMenu,
)
from cogs.casino.hide_and_seek import HidingView, LobbyView, SeekerView
from cogs.casino.mafia_views import (
    DayVoteView,
    EphemeralActionView,
    MafiaLobbyView,
    RoleRevealView,
    SecretButtonView,
)
from cogs.casino.roulette import RouletteLobby
from cogs.casino.roulette_views import KickView
from cogs.casino.wheel import WheelLobby, WheelResultPanel, publish_wheel_panel
from cogs.utility.theme import StatusPanel, embed_to_v2, wait_for_view_fixed_timeout


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


class MusicalChairsResultDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_winner_receipt_is_a_separate_message_when_delivery_succeeds(self):
        send = AsyncMock()
        lobby = SimpleNamespace(
            ctx=SimpleNamespace(channel=SimpleNamespace(id=300), send=send)
        )
        message = SimpleNamespace(edit=AsyncMock())
        view = SimpleNamespace(_panel_text=SimpleNamespace(content="# نهاية الجولة 3\nآخر لاعب خرج."))

        published = await ChairsLobby._publish_end_receipt(
            lobby,
            message,
            view,
            title="بطل الكراسي الموسيقية!",
            description="الفائز النهائي هو <@101>.",
            outcome="success",
            context="winner",
        )

        self.assertTrue(published)
        send.assert_awaited_once()
        self.assertIsInstance(send.await_args.kwargs["view"], StatusPanel)
        message.edit.assert_not_awaited()

    async def test_failed_winner_receipt_is_preserved_on_the_last_round_panel(self):
        send = AsyncMock(side_effect=RuntimeError("synthetic receipt failure"))
        lobby = SimpleNamespace(
            ctx=SimpleNamespace(channel=SimpleNamespace(id=300), send=send)
        )
        message = SimpleNamespace(edit=AsyncMock())
        view = SimpleNamespace(_panel_text=SimpleNamespace(content="# نهاية الجولة 3\nآخر لاعب خرج."))

        published = await ChairsLobby._publish_end_receipt(
            lobby,
            message,
            view,
            title="بطل الكراسي الموسيقية!",
            description="الفائز النهائي هو <@101>.",
            outcome="success",
            context="winner",
        )

        self.assertFalse(published)
        send.assert_awaited_once()
        message.edit.assert_awaited_once_with(view=view)
        self.assertIn("نهاية الجولة 3", view._panel_text.content)
        self.assertIn("بطل الكراسي الموسيقية", view._panel_text.content)
        self.assertIn("الفائز النهائي هو <@101>", view._panel_text.content)


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
            self.assertIn("# 🪑 لعبة الكراسي الموسيقية", view._panel_text.content)
            self.assertNotIn("آخر لاعب يفوز", view._panel_text.content)
            self.assertIn("اللاعبون (0/25)", view._panel_text.content)
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

    def test_geoguessr_round_result_keeps_summary_map_and_scoreboard_together(self):
        panel = GeoGuessrResultPanel(
            "نتيجة الجولة 3 من 6",
            "الدولة الصحيحة كانت اليابان.\n**تخمينات اللاعبين:**\n<@1>: اليابان (+10 نقطة)",
            outcome="neutral",
            image_names=("result_map.png", "dashboard.png"),
        )
        self.assertIsInstance(panel, LayoutView)
        text = "\n".join(
            item.content
            for item in panel.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("تخمينات اللاعبين", text)
        galleries = [
            item for item in panel.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        ]
        self.assertEqual(len(galleries), 1)
        self.assertEqual(len(galleries[0].items), 2)
        media_urls = [str(item.media.url) for item in galleries[0].items]
        self.assertIn("attachment://result_map.png", media_urls)
        self.assertIn("attachment://dashboard.png", media_urls)

    def test_geoguessr_final_winner_panel_can_show_external_avatar(self):
        panel = GeoGuessrResultPanel(
            "انتهت اللعبة",
            "الفائز <@1>",
            outcome="success",
            image_url="https://example.com/avatar.png",
        )
        gallery = next(
            item for item in panel.walk_children()
            if isinstance(item, discord.ui.MediaGallery)
        )
        self.assertIn("https://example.com/avatar.png", [str(item.media.url) for item in gallery.items])


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


class MafiaExpiredActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_target_picker_disables_and_reports_timeout(self):
        targets = [_FakeMember(5151), _FakeMember(5152)]
        view = EphemeralActionView(targets, timeout=0.1)
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        self.assertTrue(view.dropdown.disabled)
        self.assertIn("انتهى وقت الاختيار", view._panel_text.content)
        view.message.edit.assert_awaited_once_with(view=view)

    async def test_secret_action_panel_closes_when_no_choice_was_recorded(self):
        active_user = _FakeMember(5251)
        view = SecretButtonView(active_user, [active_user], [_FakeMember(5252)], "mafia")
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        self.assertTrue(view.button.disabled)
        self.assertIsNone(view.selected_id)
        self.assertIn("انتهى وقت الحركة السرية", view._panel_text.content)
        view.message.edit.assert_awaited_once_with(view=view)


    async def test_secret_action_does_not_open_parallel_private_pickers(self):
        active_user = _FakeMember(5261)
        view = SecretButtonView(active_user, [active_user], [_FakeMember(5262)], "mafia")
        view.action_open = True
        interaction = SimpleNamespace(
            user=active_user,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.btn_callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        self.assertIsNone(view.selected_id)
class MafiaRoleRevealV2Tests(unittest.TestCase):
    def test_role_reveal_is_a_private_state_injected_v2_view(self):
        player = _FakeMember(5301)
        view = RoleRevealView([player], {player: "mafia"})
        try:
            self.assertIsInstance(view, LayoutView)
            buttons = [
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual(len(buttons), 1)
            self.assertIs(buttons[0].style, discord.ButtonStyle.secondary)
            self.assertIn("تم توزيع الأدوار", view._panel_text.content)
            self.assertEqual(buttons[0].custom_id, "mafia_reveal_role")
        finally:
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
        self.assertIn("\n", view._panel_text.content)
        self.assertNotIn("\\n", view._panel_text.content)
        view.disable_controls()
        self.assertTrue(dropdowns[0].disabled)

    async def test_late_hide_selection_is_rejected_without_mutating_state(self):
        player = _FakeMember(210)
        view = HidingView([player])
        view.deadline = 0.0
        interaction = SimpleNamespace(
            user=player,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.dropdown.callback(interaction)

        self.assertEqual(view.hiding_spots, {})
        self.assertTrue(view.dropdown.disabled)
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    def test_seeker_panel_is_v2_and_has_only_a_select_action(self):
        view = SeekerView(_FakeMember(202), set())
        self.assertIsInstance(view, LayoutView)
        dropdowns = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        ]
        self.assertEqual(len(dropdowns), 1)
        self.assertIn("<@202>", view._panel_text.content)
        self.assertIn("\n", view._panel_text.content)
        self.assertNotIn("\\n", view._panel_text.content)
        view.disable_controls()
        self.assertTrue(dropdowns[0].disabled)


    async def test_late_search_selection_is_rejected_without_setting_choice(self):
        seeker = _FakeMember(220)
        view = SeekerView(seeker, set())
        view.deadline = 0.0
        interaction = SimpleNamespace(
            user=seeker,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.dropdown.callback(interaction)

        self.assertIsNone(view.choice)
        self.assertTrue(view.dropdown.disabled)
        interaction.response.send_message.assert_awaited_once()


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
            self.assertTrue(all(button.label == "🪑" for button in action_buttons))
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
            self.assertTrue(all(button.label == "💥" for button in buttons))
            self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
            action.eliminated_fools.add(members[0])
            action.disable_controls()
            self.assertTrue(all(button.disabled for button in buttons))
        finally:
            action.stop()


class RouletteDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_target_selection_is_rejected(self):
        chosen = _FakeMember(7001)
        targets = [chosen, _FakeMember(7002)]
        view = KickView(chosen, targets, False, None, round_num=2)
        view.deadline = 0.0
        select = next(
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
        )
        interaction = SimpleNamespace(
            user=chosen,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await select.callback(interaction)

        self.assertEqual(view.kicked_ids, [])
        self.assertTrue(all(item.disabled for item in view._selects))
        interaction.response.send_message.assert_awaited_once()

    async def test_late_weapon_action_does_not_consume_inventory(self):
        chosen = _FakeMember(7101)
        consumed = []
        currency = SimpleNamespace(remove_item=lambda *args: consumed.append(args) or True)
        view = KickView(chosen, [chosen, _FakeMember(7102)], True, currency, round_num=3)
        view.deadline = 0.0
        interaction = SimpleNamespace(
            user=chosen,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.weapon_callback(interaction)

        self.assertEqual(consumed, [])
        self.assertTrue(view.weapon_btn.disabled)
        interaction.response.send_message.assert_awaited_once()


class OtherGameLobbyV2Tests(unittest.IsolatedAsyncioTestCase):
    async def _assert_v2_lobby(self, view_type, expected_ids, *, player_collection="list"):
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop),
            channel=SimpleNamespace(id=100),
            author=SimpleNamespace(id=200),
        )
        if view_type in (GuessGameMenu, MafiaLobbyView):
            async def start_game(_ctx, _players):
                return None

            view = view_type(ctx, start_game=start_game)
        else:
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

    async def _assert_lobby_extension_budget(
        self,
        view_type,
        *,
        initial_time=35,
        extension_method="add_time_btn",
    ):
        loop = asyncio.get_running_loop()
        host = _FakeMember(200)
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop),
            channel=SimpleNamespace(id=100),
            author=host,
        )
        if view_type is GuessGameMenu:
            async def start_game(_ctx, _players):
                return None

            view = view_type(ctx, start_game=start_game)
        else:
            view = view_type(ctx)
        try:
            self.assertEqual(view.extensions_left, 3)
            self.assertIn("تمديدات متاحة:** 3", view._panel_text.content)

            async def click_extension():
                interaction = SimpleNamespace(
                    user=host,
                    response=SimpleNamespace(
                        is_done=lambda: False,
                        edit_message=AsyncMock(),
                        send_message=AsyncMock(),
                    ),
                    followup=SimpleNamespace(send=AsyncMock()),
                )
                await getattr(view, extension_method)(interaction, button=None)
                return interaction

            for remaining in (2, 1, 0):
                interaction = await click_extension()
                self.assertEqual(view.extensions_left, remaining)
                self.assertEqual(view.time_left, initial_time + (3 - remaining) * 10)
                self.assertIn(
                    f"تمديدات متاحة:** {remaining}",
                    view._panel_text.content,
                )
                interaction.response.edit_message.assert_awaited_once_with(view=view)

            time_button_id = {
                RouletteLobby: "roulette_add_time",
                ChairsLobby: "chairs_add_time",
                GuessGameMenu: "geoguessr_add_time",
                LobbyView: "hide_seek_add_time",
            }[view_type]
            time_button = next(
                item for item in view.walk_children()
                if isinstance(item, discord.ui.Button) and item.custom_id == time_button_id
            )
            self.assertTrue(time_button.disabled)

            capped_interaction = await click_extension()
            self.assertEqual(view.extensions_left, 0)
            self.assertEqual(view.time_left, initial_time + 30)
            self.assertTrue(capped_interaction.response.send_message.await_count == 1)

            view.game_started = True
            closed_interaction = await click_extension()
            self.assertEqual(view.extensions_left, 0)
            self.assertEqual(view.time_left, initial_time + 30)
            self.assertEqual(closed_interaction.response.send_message.await_count, 1)
        finally:
            task = getattr(view, "timer_task", None) or getattr(view, "lobby_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def test_roulette_lobby_extension_budget_is_finite(self):
        await self._assert_lobby_extension_budget(RouletteLobby)

    async def test_chairs_lobby_extension_budget_is_finite(self):
        await self._assert_lobby_extension_budget(ChairsLobby)

    async def test_geoguessr_lobby_extension_budget_is_finite(self):
        await self._assert_lobby_extension_budget(
            GuessGameMenu,
            initial_time=40,
            extension_method="add_time",
        )

    async def test_hide_and_seek_lobby_extension_budget_is_finite(self):
        await self._assert_lobby_extension_budget(
            LobbyView,
            initial_time=30,
        )

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

    async def test_wheel_keeps_each_player_spin_and_final_summary_as_separate_messages(self):
        loop = asyncio.get_running_loop()
        bot = SimpleNamespace(loop=loop, active_games={})
        channel = SimpleNamespace(id=777)
        ctx = SimpleNamespace(
            bot=bot,
            channel=channel,
            author=_FakeMember(7000),
            send=AsyncMock(),
        )
        players = [_FakeMember(7001), _FakeMember(7002)]
        view = WheelLobby(ctx)
        view.players = players

        currency = SimpleNamespace(
            settle_wheel=lambda user_id, payout, target_loss: {
                "played": True,
                "reason": None,
                "before": 100,
                "after": 150,
                "net": 50,
                "refund": 0,
            },
        )
        try:
            with (
                patch("cogs.casino.wheel.get_currency", return_value=currency),
                patch("cogs.casino.wheel.secrets.randbelow", return_value=0),
                patch("cogs.casino.wheel.render_wheel", return_value=BytesIO(b"png")),
                patch("cogs.casino.wheel.asyncio.sleep", new=AsyncMock()),
            ):
                await view.play_rounds()

            self.assertEqual(ctx.send.await_count, 3)
            posted_views = [call.kwargs["view"] for call in ctx.send.await_args_list]
            self.assertTrue(all(isinstance(item, WheelResultPanel) for item in posted_views))
            self.assertIn("نتيجة اللفة 1/2", posted_views[0]._panel_text.content)
            self.assertIn("نتيجة اللفة 2/2", posted_views[1]._panel_text.content)
            self.assertIn("خلصت جولات العجلة", posted_views[2]._panel_text.content)
            self.assertIn("attachment://wheel.png", str(next(
                item for item in posted_views[0].walk_children()
                if isinstance(item, discord.ui.MediaGallery)
            ).items[0].media.url))
        finally:
            view.lobby_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await view.lobby_task

    async def test_mafia_lobby_uses_v2_components_and_keeps_cover_gallery(self):
        loop = asyncio.get_running_loop()
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop),
            channel=SimpleNamespace(id=100),
            author=SimpleNamespace(id=200),
        )
        view = MafiaLobbyView(ctx, start_game=lambda _ctx, _players: None)
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


class WheelReceiptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_wheel_result_stays_a_separate_message(self):
        sent = SimpleNamespace(id=123)
        ctx = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            send=AsyncMock(return_value=sent),
        )
        fallback_message = SimpleNamespace(edit=AsyncMock())
        panel = WheelResultPanel("نتيجة اللفة 1/2", "صافي الرصيد: +50", "win")
        pending = []

        message = await publish_wheel_panel(
            ctx,
            panel,
            fallback_message=fallback_message,
            pending_receipts=pending,
        )

        self.assertIs(message, sent)
        ctx.send.assert_awaited_once()
        self.assertIsInstance(ctx.send.await_args.kwargs["view"], WheelResultPanel)
        fallback_message.edit.assert_not_awaited()
        self.assertEqual(pending, [])

    async def test_failed_wheel_result_is_saved_on_the_closed_lobby(self):
        ctx = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            send=AsyncMock(side_effect=RuntimeError("synthetic result delivery failure")),
        )
        fallback_message = SimpleNamespace(edit=AsyncMock())
        panel = WheelResultPanel("نتيجة اللفة 2/3", "النتيجة: +100\nصافي الرصيد: +50", "win")
        pending = []

        message = await publish_wheel_panel(
            ctx,
            panel,
            fallback_message=fallback_message,
            pending_receipts=pending,
        )

        self.assertIsNone(message)
        ctx.send.assert_awaited_once()
        self.assertEqual(len(pending), 1)
        fallback_message.edit.assert_awaited_once()
        fallback = fallback_message.edit.await_args.kwargs["view"]
        self.assertIsInstance(fallback, StatusPanel)
        text = "\n".join(
            item.content for item in fallback.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("نتيجة اللفة 2/3", text)
        self.assertIn("صافي الرصيد: +50", text)

class WheelLobbyStartRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_start_after_defer_gets_private_v2_feedback(self):
        loop = asyncio.get_running_loop()
        host = _FakeMember(9001)
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop, active_games={}),
            channel=SimpleNamespace(id=9002),
            author=host,
            send=AsyncMock(),
        )
        view = WheelLobby(ctx)
        start_button = next(
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.custom_id == "wheel_start"
        )

        async def another_click_starts_first():
            view.started = True

        interaction = SimpleNamespace(
            user=host,
            response=SimpleNamespace(
                defer=AsyncMock(side_effect=another_click_starts_first),
                is_done=lambda: True,
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        try:
            await view.start_button(interaction, start_button)
            interaction.response.defer.assert_awaited_once()
            interaction.followup.send.assert_awaited_once()
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
            ctx.send.assert_not_awaited()
        finally:
            view.lobby_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await view.lobby_task

class MafiaLobbyStartRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_start_after_defer_gets_private_v2_feedback(self):
        loop = asyncio.get_running_loop()
        host = _FakeMember(9101)
        ctx = SimpleNamespace(
            bot=SimpleNamespace(loop=loop, active_games={}),
            channel=SimpleNamespace(id=9102),
            author=host,
            send=AsyncMock(),
        )
        view = MafiaLobbyView(ctx, start_game=AsyncMock())
        start_button = next(
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.custom_id == "mafia_start"
        )

        async def another_click_starts_first():
            view.game_started = True

        interaction = SimpleNamespace(
            user=host,
            response=SimpleNamespace(
                defer=AsyncMock(side_effect=another_click_starts_first),
                is_done=lambda: True,
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        try:
            await view.start_button(interaction, start_button)
            interaction.response.defer.assert_awaited_once()
            interaction.followup.send.assert_awaited_once()
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
            view.start_game.assert_not_awaited()
            ctx.send.assert_not_awaited()
        finally:
            view.timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await view.timer_task

class GeoGuessrRoundExtensionPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_extensions_are_limited_and_button_disables_at_cap(self):
        executor = _FakeMember(8101)
        timer_state = {"end_time": 9999999999.0}
        view = ActiveGameView(executor, timer_state, round_num=1, total_rounds=6)
        initial_end = timer_state["end_time"]

        def interaction_for(user):
            return SimpleNamespace(
                user=user,
                response=SimpleNamespace(send_message=AsyncMock()),
            )

        try:
            self.assertEqual(view.extensions_left, 2)
            for remaining in (1, 0):
                interaction = interaction_for(executor)
                await view.add_time(interaction)
                self.assertEqual(view.extensions_left, remaining)
                self.assertEqual(timer_state["end_time"], initial_end + (2 - remaining) * 10)
                self.assertIn(
                    f"تمديدات متاحة:** {remaining}",
                    view._panel_text.content,
                )
                interaction.response.send_message.assert_awaited_once()
            self.assertTrue(view.button.disabled)

            capped = interaction_for(executor)
            await view.add_time(capped)
            self.assertEqual(timer_state["end_time"], initial_end + 20)
            self.assertEqual(view.extensions_left, 0)
            self.assertTrue(capped.response.send_message.await_args.kwargs["ephemeral"])
            self.assertIn("وصلنا للحد الأقصى", "\\n".join(
                item.content
                for item in capped.response.send_message.await_args.kwargs["view"].walk_children()
                if isinstance(item, discord.ui.TextDisplay)
            ))
        finally:
            view.stop()

    async def test_expired_round_cannot_be_reopened_by_a_stale_button(self):
        executor = _FakeMember(8102)
        timer_state = {"end_time": __import__("time").monotonic() - 1}
        view = ActiveGameView(executor, timer_state, round_num=2, total_rounds=6)
        interaction = SimpleNamespace(
            user=executor,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await view.add_time(interaction)

        self.assertTrue(view.round_closed)
        self.assertTrue(view.button.disabled)
        self.assertLess(timer_state["end_time"], __import__("time").monotonic())
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        view.stop()


class FixedDeadlineWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_stops_view_even_if_native_timeout_would_be_renewed(self):
        class FakeView:
            def __init__(self):
                self.done = asyncio.Event()
                self.stopped = False

            async def wait(self):
                await self.done.wait()
                return False

            def stop(self):
                self.stopped = True
                self.done.set()

        view = FakeView()
        expired = await wait_for_view_fixed_timeout(view, 0.01)

        self.assertTrue(expired)
        self.assertTrue(view.stopped)

    async def test_natural_view_completion_is_not_reported_as_deadline_expiry(self):
        class FakeView:
            def __init__(self):
                self.stopped = False

            async def wait(self):
                return False

            def stop(self):
                self.stopped = True

        view = FakeView()
        expired = await wait_for_view_fixed_timeout(view, 1.0)

        self.assertFalse(expired)
        self.assertFalse(view.stopped)


if __name__ == "__main__":
    unittest.main()
