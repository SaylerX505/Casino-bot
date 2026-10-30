"""Preflight tests: insufficient balances must be rejected before a game panel is sent."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.casino.blackjack import Blackjack
from cogs.casino.solo_advanced import AdvancedSoloGames, MinesView
from cogs.casino.solo_betting import SoloBetting
from cogs.casino.rps import RPS
from cogs.utility.theme import StatusPanel


def make_context(user_id=101):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
        interaction=None,
        prefix="+",
        command=SimpleNamespace(name="coinflip"),
        send=AsyncMock(),
        guild=SimpleNamespace(id=202),
        channel=SimpleNamespace(id=303),
    )


def panel_text(panel):
    return "\\n".join(
        item.content
        for item in panel.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


class WagerPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_instant_solo_bet_rejects_before_creating_the_choice_panel(self):
        ctx = make_context()
        cog = SimpleNamespace(_wagers_ready=True)

        with patch("cogs.casino.solo_betting.bank.get_balance", return_value=25):
            await SoloBetting._start_wager(
                cog,
                ctx,
                "100",
                game_type="coinflip",
                game_title="رمي العملة",
                choices={},
                payout_numerator=190,
                odds_description="1 من 2",
                instructions="اختار صورة أو كتابة.",
                command_name="coinflip",
            )

        ctx.send.assert_awaited_once()
        sent_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(sent_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(sent_view))
        self.assertIn("25", panel_text(sent_view))
        self.assertIn("100", panel_text(sent_view))
        self.assertIn("مفيش رصيد اتخصم", panel_text(sent_view))

    async def test_interactive_solo_bet_rejects_before_opening_mines(self):
        ctx = make_context()
        solo_cog = SimpleNamespace(_wagers_ready=True)
        bot = SimpleNamespace(get_cog=lambda name: solo_cog if name == "SoloBetting" else None)
        cog = AdvancedSoloGames(bot)

        with patch("cogs.casino.solo_advanced.bank.get_balance", return_value=50):
            await cog._start_interactive(ctx, "100", MinesView, 3)

        ctx.send.assert_awaited_once()
        sent_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(sent_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(sent_view))
        self.assertIn("50", panel_text(sent_view))
        self.assertIn("100", panel_text(sent_view))

    async def test_blackjack_rejects_before_opening_the_table(self):
        ctx = make_context()
        cog = Blackjack(SimpleNamespace())
        cog._wagers_ready = True

        with patch("cogs.casino.blackjack.bank.get_balance", return_value=99):
            await Blackjack.blackjack.callback(cog, ctx, "100")

        ctx.send.assert_awaited_once()
        sent_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(sent_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(sent_view))
        self.assertIn("99", panel_text(sent_view))
        self.assertIn("100", panel_text(sent_view))

    async def test_rps_checks_both_players_before_posting_the_challenge(self):
        ctx = make_context()
        opponent = SimpleNamespace(id=404, mention="<@404>", bot=False)
        currency = object()
        bot = SimpleNamespace(
            active_games={},
            get_cog=lambda name: currency if name == "Currency" else None,
        )
        cog = RPS(bot)
        cog._escrow_ready = True

        with (
            patch("cogs.casino.rps.bank.get_balance", side_effect=[50, 500]),
            patch("cogs.casino.rps.register_game_task") as register_game,
        ):
            await RPS.rps.callback(cog, ctx, "100", opponent)

        register_game.assert_not_called()
        ctx.send.assert_awaited_once()
        sent_view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(sent_view, StatusPanel)
        self.assertIn("رصيدك مش كفاية", panel_text(sent_view))
        self.assertIn("50", panel_text(sent_view))
        self.assertIn("100", panel_text(sent_view))


if __name__ == "__main__":
    unittest.main()
