"""Regression tests for Blackjack scoring, outcomes, and Components V2 controls."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ui import Container, LayoutView

from cogs.casino.blackjack import (
    BLACKJACK_BRAND,
    BlackjackView,
    _dealer_display_cards,
    _final_outcome,
    _hand_value,
    _initial_outcome,
    _is_natural_blackjack,
    render_blackjack_table,
)


class BlackjackArtTests(unittest.TestCase):
    def test_blackjack_art_uses_the_requested_ckr_brand(self):
        self.assertEqual(BLACKJACK_BRAND, "CKR")

    def test_generated_table_is_a_png_and_hides_the_hole_card_in_play(self):
        player = [("10", "♠"), ("7", "♥")]
        dealer = [("9", "♣"), ("8", "♦")]

        playing_row = _dealer_display_cards(dealer, "playing")
        finished_row = _dealer_display_cards(dealer, "finished")
        self.assertEqual(playing_row, [(dealer[0], False), (None, True)])
        self.assertEqual(finished_row, [(dealer[0], False), (dealer[1], False)])

        playing_png = render_blackjack_table(player, dealer, phase="playing", amount=100)
        finished_png = render_blackjack_table(player, dealer, phase="finished", amount=100)
        self.assertTrue(playing_png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(playing_png), 5000)
        self.assertNotEqual(playing_png, finished_png)


class BlackjackScoringTests(unittest.TestCase):
    def test_hand_value_handles_soft_and_hard_aces(self):
        self.assertEqual(_hand_value([("A", "♠"), ("K", "♥")]), 21)
        self.assertEqual(_hand_value([("A", "♠"), ("6", "♥")]), 17)
        self.assertEqual(_hand_value([("A", "♠"), ("6", "♥"), ("10", "♦")]), 17)
        self.assertEqual(_hand_value([("A", "♠"), ("A", "♥"), ("9", "♦")]), 21)
        self.assertEqual(
            _hand_value([("A", "♠"), ("A", "♥"), ("9", "♦"), ("9", "♣")]),
            20,
        )

    def test_natural_requires_exactly_two_cards(self):
        self.assertTrue(_is_natural_blackjack([("A", "♠"), ("K", "♥")]))
        self.assertFalse(_is_natural_blackjack([("A", "♠"), ("5", "♥"), ("5", "♦")]))
        self.assertFalse(_is_natural_blackjack([("10", "♠"), ("9", "♥")]))

    def test_initial_naturals_resolve_as_natural_loss_or_push(self):
        natural = [("A", "♠"), ("K", "♥")]
        ordinary = [("10", "♦"), ("7", "♣")]
        self.assertEqual(_initial_outcome(natural, natural), "push")
        self.assertEqual(_initial_outcome(natural, ordinary), "natural")
        self.assertEqual(_initial_outcome(ordinary, natural), "loss")
        self.assertIsNone(_initial_outcome(ordinary, ordinary))

    def test_final_outcomes_cover_bust_win_loss_and_push(self):
        self.assertEqual(
            _final_outcome([("10", "♠"), ("9", "♥"), ("5", "♦")], [("10", "♣"), ("7", "♠")]),
            "loss",
        )
        self.assertEqual(
            _final_outcome([("10", "♠"), ("8", "♥")], [("10", "♣"), ("8", "♠"), ("5", "♦")]),
            "win",
        )
        self.assertEqual(
            _final_outcome([("10", "♠"), ("9", "♥")], [("10", "♣"), ("9", "♠")]),
            "push",
        )
        self.assertEqual(
            _final_outcome([("10", "♠"), ("9", "♥")], [("10", "♣"), ("8", "♠")]),
            "win",
        )
        self.assertEqual(
            _final_outcome([("10", "♠"), ("7", "♥")], [("10", "♣"), ("9", "♠")]),
            "loss",
        )


class BlackjackViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = SimpleNamespace(id=101, mention="<@101>")
        self.view = BlackjackView(SimpleNamespace(), self.owner, 100, deck=[])

    async def asyncTearDown(self):
        self.view.stop()

    def test_blackjack_waiting_panel_uses_v2_and_secondary_controls(self):
        self.assertIsInstance(self.view, LayoutView)
        text = "\n".join(
            item.content for item in self.view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("# BLACKJACK", text)
        self.assertIn("**Bet:** 100", text)
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual({button.label for button in buttons}, {"Deal", "Cancel"})
        styles = {button.label: button.style for button in buttons}
        self.assertTrue(all(style is discord.ButtonStyle.secondary for style in styles.values()))

    def test_blackjack_panel_uses_gold_accent(self):
        container = next(
            item for item in self.view.walk_children()
            if isinstance(item, Container)
        )
        self.assertEqual(container.accent_color, 0xF5C542)

    def test_playing_phase_offers_double_down_only_before_the_first_hit(self):
        self.view.phase = "playing"
        self.view.player_cards = [("10", "♠"), ("7", "♥")]
        self.view.dealer_cards = [("9", "♣"), ("8", "♦")]
        self.view._render_view()
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(
            {button.label for button in buttons},
            {"Hit", "Stand", "Double"},
        )
        self.assertEqual(len({button.custom_id for button in buttons}), 3)

        self.view.player_cards.append(("2", "♦"))
        self.view._render_view()
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual({button.label for button in buttons}, {"Hit", "Stand"})


    async def test_double_down_adds_one_card_and_stands_automatically(self):
        self.view.phase = "playing"
        self.view.game_id = "blackjack-double-down-ui"
        self.view.player_cards = [("10", "♠"), ("7", "♥")]
        self.view.dealer_cards = [("10", "♣"), ("7", "♠")]
        self.view.deck = [("2", "♦")]
        message = SimpleNamespace(id=555, edit=AsyncMock())
        self.view.message = message
        self.view._double_down_wager = AsyncMock(
            return_value={"doubled": True, "stake": 200, "balance_after": 800}
        )
        self.view._settle_outcome = AsyncMock()
        interaction = SimpleNamespace(
            message=message,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self.view.double_down(interaction)

        self.assertEqual(self.view.amount, 200)
        self.assertEqual(self.view.player_cards[-1], ("2", "♦"))
        self.assertTrue(self.view.doubled_down)
        self.assertTrue(self.view.resolving)
        self.view._double_down_wager.assert_awaited_once()
        self.view._settle_outcome.assert_awaited_once_with("win")
        interaction.followup.send.assert_not_awaited()

    async def test_double_down_with_insufficient_funds_keeps_normal_actions_available(self):
        self.view.phase = "playing"
        self.view.game_id = "blackjack-double-down-no-balance"
        self.view.player_cards = [("10", "♠"), ("7", "♥")]
        self.view.dealer_cards = [("10", "♣"), ("7", "♠")]
        message = SimpleNamespace(id=556, edit=AsyncMock())
        self.view.message = message
        self.view._double_down_wager = AsyncMock(
            return_value={"doubled": False, "reason": "insufficient_funds", "balance": 20}
        )
        interaction = SimpleNamespace(
            message=message,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self.view.double_down(interaction)

        self.assertEqual(self.view.amount, 100)
        self.assertFalse(self.view.doubled_down)
        self.assertFalse(self.view.resolving)
        self.assertEqual(self.view.phase, "playing")
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        controls = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(
            {button.label for button in controls},
            {"Hit", "Stand", "Double"},
        )

    async def test_cancelled_waiting_hand_refreshes_the_table_art(self):
        art_file = object()
        self.view._art_file = AsyncMock(return_value=art_file)
        message = SimpleNamespace(id=991)
        interaction = SimpleNamespace(
            message=message,
            response=SimpleNamespace(edit_message=AsyncMock()),
        )

        await self.view.cancel(interaction)

        interaction.response.edit_message.assert_awaited_once_with(
            view=self.view,
            attachments=[art_file],
        )
        self.assertTrue(self.view.done)
        self.assertEqual(self.view.phase, "finished")
        self.assertIn("No bet was placed.", self.view.notice)
        self.assertFalse(
            any(isinstance(item, discord.ui.Button) for item in self.view.walk_children())
        )

    async def test_final_result_stays_in_the_original_game_panel(self):
        user_context = SimpleNamespace(interaction=object(), send=AsyncMock())
        self.view.context = user_context
        self.view.player_cards = [("10", "♠"), ("7", "♥"), ("2", "♦")]
        self.view.dealer_cards = [("10", "♣"), ("7", "♠")]
        self.view.amount = 200
        self.view.doubled_down = True
        self.view.receipt = {
            "status": "settled",
            "outcome": "win",
            "stake": 200,
            "payout": 400,
            "balance_after": 1000,
        }
        self.view.result_kind = "win"
        self.view._edit = AsyncMock()

        await self.view._apply_settlement(self.view.receipt)

        self.view._edit.assert_awaited_once()
        user_context.send.assert_not_awaited()
        rendered_text = self.view._finished_content()
        self.assertIn("# WIN · BLACKJACK", rendered_text)
        self.assertIn("**You:**", rendered_text)
        self.assertIn("**Dealer:**", rendered_text)
        self.assertIn("Bet: 200", rendered_text)
        self.assertIn("Bonus: **+200", rendered_text)
        self.assertIn("**Balance:** 1,000", rendered_text)
        self.view._render_view()
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual({button.label for button in buttons}, {"Play Again"})

if __name__ == "__main__":
    unittest.main()
