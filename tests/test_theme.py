"""Regression tests for shared result presentation."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.utility.theme import THEME, StatusPanel, lobby_embed, private_status, report_game_failure, result_embed, try_edit_view


class ResultEmbedTests(unittest.TestCase):
    def test_win_uses_shared_brand_palette_and_supports_winner_avatar(self):
        embed = result_embed(
            title="Winner",
            description="Game complete",
            outcome="win",
            thumbnail_url="https://example.com/avatar.png",
        )

        self.assertEqual(embed.color.value, THEME["gold"])
        self.assertEqual(embed.thumbnail.url, "https://example.com/avatar.png")
        self.assertIn("Çukur", embed.footer.text)

    def test_loss_and_unknown_outcomes_have_safe_fallback_colors(self):
        loss = result_embed("Loss", "No winner", outcome="loss")
        unknown = result_embed("Neutral", "No result", outcome="not-a-real-state")

        self.assertEqual(loss.color.value, THEME["red_dark"])
        self.assertEqual(unknown.color.value, THEME["grey"])



class SharedPanelBrandingTests(unittest.TestCase):
    def test_status_panel_contains_the_shared_brand_footer(self):
        panel = StatusPanel("Test", "Body text", "success")
        content = "\n".join(
            item.content
            for item in panel.walk_children()
            if hasattr(item, "content")
        )
        self.assertIn("Body text", content)
        self.assertIn("كازينو الحفرة", content)

    def test_lobby_host_is_mentionable_in_the_roster_panel(self):
        host = SimpleNamespace(display_name="Lobby host", mention="<@123>")
        panel = lobby_embed(
            "Game lobby",
            "Rules",
            [],
            30,
            host=host,
            min_players=2,
        )
        self.assertIn("<@123>", panel.description)
        self.assertIn("الحد الأدنى للبدء", panel.description)


class PrivateStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_interaction_response_uses_private_v2_panel(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(
                is_done=lambda: False,
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await private_status(interaction, "تنبيه", "رسالة خاصة")

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertTrue(kwargs["ephemeral"])
        interaction.followup.send.assert_not_awaited()

    async def test_already_acknowledged_interaction_uses_private_followup(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(
                is_done=lambda: True,
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await private_status(interaction, "تنبيه", "رسالة خاصة")

        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertTrue(kwargs["ephemeral"])
        interaction.response.send_message.assert_not_awaited()


class GameFailureReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disables_active_game_panel_and_sends_separate_failure_receipt(self):
        message = SimpleNamespace(edit=AsyncMock())
        active_view = SimpleNamespace(
            message=message,
            _disable_buttons=Mock(),
            stop=Mock(),
        )
        bot = SimpleNamespace(
            active_games={10: {"view": active_view}}
        )
        ctx = SimpleNamespace(
            bot=bot,
            channel=SimpleNamespace(id=10),
            send=AsyncMock(),
        )

        reported = await report_game_failure(
            ctx,
            "Game stopped",
            "The round failed safely.",
        )

        self.assertTrue(reported)
        active_view._disable_buttons.assert_called_once()
        active_view.stop.assert_called_once()
        message.edit.assert_awaited_once_with(view=active_view)
        ctx.send.assert_awaited_once()
        sent_kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(sent_kwargs["view"], StatusPanel)
        self.assertIs(sent_kwargs["allowed_mentions"].everyone, False)
        self.assertTrue(bot.active_games[10]["failure_reported"])



    async def test_repeated_failure_reports_are_suppressed_for_the_same_game(self):
        message = SimpleNamespace(edit=AsyncMock())
        active_view = SimpleNamespace(message=message, _disable_buttons=Mock(), stop=Mock())
        ctx = SimpleNamespace(
            bot=SimpleNamespace(active_games={10: {"view": active_view, "failure_reported": True}}),
            channel=SimpleNamespace(id=10),
            send=AsyncMock(),
        )

        reported = await report_game_failure(ctx, "Game stopped", "Duplicate report")

        self.assertFalse(reported)
        message.edit.assert_not_awaited()
        ctx.send.assert_not_awaited()

    async def test_sends_failure_panel_when_no_active_message_exists(self):
        ctx = SimpleNamespace(
            bot=SimpleNamespace(active_games={}),
            channel=SimpleNamespace(id=11),
            send=AsyncMock(),
        )

        reported = await report_game_failure(
            ctx,
            "Game stopped",
            "The round failed safely.",
        )

        self.assertTrue(reported)
        ctx.send.assert_awaited_once()
        self.assertIsInstance(ctx.send.await_args.kwargs["view"], StatusPanel)


class SafeViewEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_edit_failure_is_logged_and_does_not_escape(self):
        import discord

        response = SimpleNamespace(status=503, reason="synthetic failure")
        message = SimpleNamespace(
            edit=AsyncMock(
                side_effect=discord.HTTPException(response, "synthetic lobby edit failure")
            )
        )
        view = StatusPanel("Lobby", "Safe fallback")

        with patch("cogs.utility.theme.logging.getLogger") as get_logger:
            logger = get_logger.return_value
            updated = await try_edit_view(message, view, context="test_lobby_start")

        self.assertFalse(updated)
        message.edit.assert_awaited_once_with(view=view)
        logger.warning.assert_called_once()

    async def test_missing_lobby_message_is_a_safe_noop(self):
        view = StatusPanel("Lobby", "Safe fallback")
        updated = await try_edit_view(None, view, context="test_lobby_start")
        self.assertFalse(updated)


if __name__ == "__main__":
    unittest.main()
