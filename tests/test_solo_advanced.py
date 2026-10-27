"""Regression tests for the new interactive and instant solo betting games."""

import unittest
from unittest.mock import patch

from cogs.casino import solo_advanced
from cogs.casino.permissions import GAME_COMMAND_ALIASES, GAME_LABELS


class SoloAdvancedGameTests(unittest.TestCase):
    def test_mines_multiplier_starts_at_one_and_grows_after_safe_reveals(self):
        self.assertEqual(solo_advanced._mines_multiplier(3, 0), 1.0)
        self.assertGreater(solo_advanced._mines_multiplier(3, 1), 1.0)
        self.assertGreater(
            solo_advanced._mines_multiplier(3, 4),
            solo_advanced._mines_multiplier(3, 3),
        )

    def test_mines_multiplier_rejects_invalid_ranges(self):
        for mines_count, safe_reveals in ((0, 0), (9, 0), (3, -1), (3, 18)):
            with self.subTest(mines_count=mines_count, safe_reveals=safe_reveals), self.assertRaises(ValueError):
                solo_advanced._mines_multiplier(mines_count, safe_reveals)

    def test_crash_point_is_finite_and_within_advertised_bounds(self):
        for _ in range(500):
            point = solo_advanced._crash_point()
            self.assertGreaterEqual(point, 1.0)
            self.assertLessEqual(point, solo_advanced.CRASH_MAX_MULTIPLIER)
            self.assertEqual(round(point, 2), point)

    def test_plinko_extreme_bin_has_three_times_gross_return(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=0):
            result, won, payout = solo_advanced._plinko_drop("drop", 100)
        self.assertEqual(result, "slot=0;multiplier=3x")
        self.assertTrue(won)
        self.assertEqual(payout, 300)

    def test_plinko_center_bin_loses_the_stake(self):
        # Cumulative weights before the center bin are 1 + 8 + 28 + 56 = 93.
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=93):
            result, won, payout = solo_advanced._plinko_drop("drop", 100)
        self.assertEqual(result, "slot=4;multiplier=0x")
        self.assertFalse(won)
        self.assertEqual(payout, 0)

    def test_number_roulette_wins_only_on_the_exact_number(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=17):
            result, won, payout = solo_advanced._number_roulette("17", 100)
        self.assertEqual(result, "17")
        self.assertTrue(won)
        self.assertEqual(payout, 3_600)

        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=17):
            result, won, payout = solo_advanced._number_roulette("18", 100)
        self.assertEqual(result, "17")
        self.assertFalse(won)
        self.assertEqual(payout, 0)

    def test_number_roulette_accepts_zero_and_rejects_out_of_range(self):
        with patch("cogs.casino.solo_advanced.secrets.randbelow", return_value=0):
            result, won, payout = solo_advanced._number_roulette("0", 10)
        self.assertEqual(result, "0")
        self.assertTrue(won)
        self.assertEqual(payout, 360)

        for choice in ("-1", "37", "not-a-number", "١٧"):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                solo_advanced._number_roulette(choice, 100)

    def test_new_games_are_registered_for_channel_policy(self):
        expected = {
            "mines": "Mines",
            "crash": "Crash",
            "plinko": "Plinko",
            "number_roulette": "روليت الأرقام",
            "hilo": "Hi-Lo",
        }
        for key, label in expected.items():
            with self.subTest(game=key):
                self.assertEqual(GAME_LABELS[key], label)
        for command, key in (
            ("mines", "mines"),
            ("crash", "crash"),
            ("plinko", "plinko"),
            ("numberroulette", "number_roulette"),
            ("hilo", "hilo"),
        ):
            with self.subTest(command=command):
                self.assertEqual(GAME_COMMAND_ALIASES[command], key)

    def test_game_art_generates_png_for_each_new_game(self):
        for game_type in ("mines", "crash", "hilo"):
            with self.subTest(game_type=game_type):
                image = solo_advanced._render_live_art(game_type)
                self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        for game_type in ("plinko", "number_roulette"):
            with self.subTest(game_type=game_type):
                image = solo_advanced._render_advanced_instant_art(game_type)
                self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
