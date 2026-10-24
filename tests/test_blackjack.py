"""Regression tests for Blackjack scoring, outcomes, and Components V2 controls."""

import unittest
from types import SimpleNamespace

import discord
from discord.ui import Container, LayoutView

from cogs.casino.blackjack import (
    BlackjackView,
    _dealer_display_cards,
    _final_outcome,
    _hand_value,
    _initial_outcome,
    _is_natural_blackjack,
    render_blackjack_table,
)


class BlackjackArtTests(unittest.TestCase):
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
        self.assertIn("2.0x", text)
        self.assertIn("2.5x", text)
        self.assertIn("التعادل", text)
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual({button.label for button in buttons}, {"ابدأ اللعب", "إلغاء"})
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))

    def test_blackjack_panel_uses_gold_accent(self):
        container = next(
            item for item in self.view.walk_children()
            if isinstance(item, Container)
        )
        self.assertEqual(container.accent_color, 0xF5C542)

    def test_playing_phase_exposes_hit_and_stand_not_start_or_cancel(self):
        self.view.phase = "playing"
        self.view.player_cards = [("10", "♠"), ("7", "♥")]
        self.view.dealer_cards = [("9", "♣"), ("8", "♦")]
        self.view._render_view()
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual({button.label for button in buttons}, {"اسحب بطاقة", "قف"})
        self.assertEqual(len({button.custom_id for button in buttons}), 2)


if __name__ == "__main__":
    unittest.main()
