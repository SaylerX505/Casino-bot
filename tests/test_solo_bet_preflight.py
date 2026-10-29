"""Ensure insufficient wagers are rejected before solo-game panels are sent."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.casino import solo_advanced, solo_betting
from cogs.casino.blackjack import Blackjack
from cogs.utility.theme import StatusPanel


def make_context():
    return SimpleNamespace(
        author=SimpleNamespace(id=101, mention="<@101>"),
        interaction=None,
        prefix="!",
        send=AsyncMock(),
    )


def panel_text(view):
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


class SoloBetPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_instant_solo_bets_reject_before_view_is_constructed(self):
        cog = solo_betting.SoloBetting(SimpleNamespace())
        cog._wagers_ready = True
        ctx = make_context()
        with (
            patch("cogs.casino.solo_betting.has_active_solo_session", return_value=False),
            patch("cogs.casino.solo_betting.bank.get_balance", return_value=0),
            patch("cogs.casino.solo_betting.SoloWagerView") as view_constructor,
            patch("cogs.casino.solo_betting.claim_solo_session") as claim_session,
        ):
            await cog._start_wager(
                ctx,
                "100",
                game_type="coinflip",
                game_title="رمي العملة",
                choices=solo_betting.SIDES,
                payout_numerator=190,
                odds_description="1 من 2",
                instructions="اختار صورة أو كتابة.",
                command_name="coinflip",
            )

        ctx.send.assert_awaited_once()
        result_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(result_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(result_view))
        self.assertIn("100", panel_text(result_view))
        view_constructor.assert_not_called()
        claim_session.assert_not_called()

    async def test_interactive_solo_bets_reject_before_start_panel(self):
        solo_cog = SimpleNamespace(_wagers_ready=True)
        bot = SimpleNamespace(get_cog=lambda name: solo_cog if name == "SoloBetting" else None)
        cog = solo_advanced.AdvancedSoloGames(bot)
        ctx = make_context()

        with (
            patch("cogs.casino.solo_advanced.has_active_solo_session", return_value=False),
            patch("cogs.casino.solo_advanced.bank.get_balance", return_value=0),
            patch("cogs.casino.solo_advanced.MinesView") as view_constructor,
            patch("cogs.casino.solo_advanced.claim_solo_session") as claim_session,
        ):
            await cog._start_interactive(ctx, "100", solo_advanced.MinesView)

        ctx.send.assert_awaited_once()
        result_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(result_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(result_view))
        view_constructor.assert_not_called()
        claim_session.assert_not_called()

    async def test_blackjack_rejects_before_table_is_constructed(self):
        cog = Blackjack(SimpleNamespace())
        cog._wagers_ready = True
        ctx = make_context()

        with (
            patch("cogs.casino.blackjack.bank.get_balance", return_value=0),
            patch("cogs.casino.blackjack.BlackjackView") as view_constructor,
            patch("cogs.casino.blackjack.claim_solo_session") as claim_session,
        ):
            await cog.blackjack(ctx, "100")

        ctx.send.assert_awaited_once()
        result_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(result_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(result_view))
        view_constructor.assert_not_called()
        claim_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
