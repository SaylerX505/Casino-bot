"""Boundary tests for parsing user-provided bet amounts."""

import unittest

from cogs.casino.bet_validation import parse_bet_amount


class BetAmountValidationTests(unittest.TestCase):
    def test_valid_amounts_and_commas_are_supported(self):
        self.assertEqual(parse_bet_amount("100", max_bet=5000), 100)
        self.assertEqual(parse_bet_amount("1,000", max_bet=5000), 1000)
        self.assertEqual(parse_bet_amount("000100", max_bet=5000), 100)

    def test_compact_k_m_and_b_suffixes_are_supported_safely(self):
        self.assertEqual(parse_bet_amount("1k", max_bet=5000), 1000)
        self.assertEqual(parse_bet_amount("2K", max_bet=5000), 2000)
        self.assertEqual(parse_bet_amount("1.5k", max_bet=5000), 1500)
        self.assertEqual(parse_bet_amount("1.25k", max_bet=5000), 1250)
        self.assertEqual(parse_bet_amount("0.5k", max_bet=5000), 500)
        self.assertEqual(parse_bet_amount("5,000", max_bet=5000), 5000)
        self.assertEqual(parse_bet_amount("1m", max_bet=5000), 5001)
        self.assertEqual(parse_bet_amount("1b", max_bet=5000), 5001)
        self.assertEqual(parse_bet_amount("1.2345k", max_bet=5000), 0)
        self.assertEqual(parse_bet_amount("1.5", max_bet=5000), 0)
        self.assertEqual(parse_bet_amount("k", max_bet=5000), 0)

    def test_invalid_and_non_ascii_amounts_are_rejected(self):
        for value in ("", None, "abc", "-10", "1.5", "١٠٠", "10 coins"):
            with self.subTest(value=value):
                self.assertEqual(parse_bet_amount(value, max_bet=5000), 0)

    def test_unbounded_digit_strings_never_need_integer_conversion(self):
        self.assertEqual(parse_bet_amount("9" * 100_000, max_bet=5000), 5001)
        self.assertEqual(parse_bet_amount("0" * 100_000 + "100", max_bet=5000), 100)

    def test_values_over_max_return_a_rejected_sentinel(self):
        self.assertEqual(parse_bet_amount("5001", max_bet=5000), 5001)
        self.assertEqual(parse_bet_amount("99999", max_bet=5000), 5001)

    def test_invalid_maximum_is_rejected(self):
        for maximum in (True, -1, "5000"):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                parse_bet_amount("100", max_bet=maximum)


if __name__ == "__main__":
    unittest.main()
