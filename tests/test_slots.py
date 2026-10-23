"""Unit tests for the slots game's payout schedule and random outcome classification."""

import unittest
from itertools import product
from unittest.mock import patch

from cogs.casino.solo_betting import SLOT_TRIPLE_PAYOUTS, _spin_slots


class SlotsOutcomeTests(unittest.TestCase):
    def test_matching_pair_returns_documented_gross_payout(self):
        with patch(
            "cogs.casino.solo_betting.secrets.choice",
            side_effect=["🍒", "🍒", "🍋"],
        ):
            _result, won, payout = _spin_slots("spin", 100)

        self.assertEqual(result, "🍒 · 🍒 · 🍋")
        self.assertTrue(won)
        self.assertEqual(payout, 170)

    def test_triple_payout_uses_symbol_specific_multiplier(self):
        for symbol, multiplier in SLOT_TRIPLE_PAYOUTS.items():
            with self.subTest(symbol=symbol), patch(
                "cogs.casino.solo_betting.secrets.choice",
                side_effect=[symbol, symbol, symbol],
            ):
                result, won, payout = _spin_slots("spin", 100)

            self.assertEqual(result, f"{symbol} · {symbol} · {symbol}")
            self.assertTrue(won)
            self.assertEqual(payout, multiplier)

    def test_three_distinct_symbols_are_a_loss(self):
        with patch(
            "cogs.casino.solo_betting.secrets.choice",
            side_effect=["🍒", "🍋", "💎"],
        ):
            result, won, payout = _spin_slots("spin", 100)

        self.assertEqual(result, "🍒 · 🍋 · 💎")
        self.assertFalse(won)
        self.assertEqual(payout, 0)


    def test_entire_paytable_matches_published_win_rate_and_rtp(self):
        outcomes = 0
        winning_outcomes = 0
        total_gross_payout = 0
        pair_outcomes = 0
        losing_outcomes = 0

        for symbols in product(SLOT_TRIPLE_PAYOUTS, repeat=3):
            with patch(
                "cogs.casino.solo_betting.secrets.choice",
                side_effect=symbols,
            ):
                result, won, payout = _spin_slots("spin", 100)

            outcomes += 1
            total_gross_payout += payout
            if won:
                winning_outcomes += 1
            else:
                losing_outcomes += 1

            if len(set(symbols)) == 2:
                pair_outcomes += 1
                self.assertEqual(payout, 170)
            elif len(set(symbols)) == 3:
                self.assertFalse(won)
                self.assertEqual(payout, 0)
            else:
                self.assertTrue(won)
                self.assertEqual(payout, SLOT_TRIPLE_PAYOUTS[symbols[0]])

        self.assertEqual(outcomes, 216)
        self.assertEqual(winning_outcomes, 96)
        self.assertEqual(pair_outcomes, 90)
        self.assertEqual(losing_outcomes, 120)
        self.assertEqual(total_gross_payout, 20_400)
        self.assertAlmostEqual(total_gross_payout / (outcomes * 100), 0.9444444444, places=9)

    def test_invalid_stakes_are_rejected_before_spinning(self):
        for stake in (True, 0, -1, 1.5, "100"):
            with self.subTest(stake=stake), self.assertRaises(ValueError):
                _spin_slots("spin", stake)


if __name__ == "__main__":
    unittest.main()
