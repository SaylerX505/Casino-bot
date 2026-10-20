"""Regression tests for transparent wheel accounting."""
import unittest

from cogs.casino.wheel import COIN, FEE, format_wheel_breakdown


class WheelAccountingTests(unittest.TestCase):
    def test_loss_shows_entry_fee_and_additional_penalty(self):
        breakdown = format_wheel_breakdown(0, 50, 50)

        self.assertIn(f"رسوم الدخول", breakdown)
        self.assertIn(f"−{FEE:,} {COIN}", breakdown)
        self.assertIn("الخصم الإضافي", breakdown)
        self.assertIn(f"−50 {COIN}", breakdown)

    def test_partial_penalty_reports_actual_charge_and_requested_amount(self):
        breakdown = format_wheel_breakdown(0, 50, 20)

        self.assertIn(f"−20 {COIN}", breakdown)
        self.assertIn("المطلوب حسب الخانة 50", breakdown)

    def test_reward_shows_gross_payout_separately_from_entry_fee(self):
        breakdown = format_wheel_breakdown(100, 0, 0)

        self.assertIn(f"رسوم الدخول", breakdown)
        self.assertIn(f"المكافأة المصروفة", breakdown)
        self.assertIn(f"+100 {COIN}", breakdown)

    def test_zero_reward_still_reports_paid_entry_fee(self):
        breakdown = format_wheel_breakdown(0, 0, 0)

        self.assertIn(f"رسوم الدخول", breakdown)
        self.assertIn(f"المكافأة المصروفة:** 0 {COIN}", breakdown)


if __name__ == "__main__":
    unittest.main()
