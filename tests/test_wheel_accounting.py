"""Regression tests for transparent wheel accounting."""
import unittest

from cogs.casino.wheel import COIN, FEE, format_wheel_breakdown


class WheelAccountingTests(unittest.TestCase):
    def test_loss_shows_total_loss_without_double_charge(self):
        breakdown = format_wheel_breakdown(0, 50, 0)

        self.assertIn(f"−{FEE:,} {COIN}", breakdown)
        self.assertIn(f"**الخسارة الإجمالية:** −50 {COIN}", breakdown)
        self.assertNotIn("الخصم الإضافي", breakdown)

    def test_lower_loss_refunds_the_unused_entry_fee(self):
        breakdown = format_wheel_breakdown(0, 30, 20)

        self.assertIn(f"**الخسارة الإجمالية:** −30 {COIN}", breakdown)
        self.assertIn(f"**المردود من رسوم الدخول:** +20 {COIN}", breakdown)

    def test_reward_shows_gross_payout_separately_from_entry_fee(self):
        breakdown = format_wheel_breakdown(100, None, 0)

        self.assertIn(f"رسوم الدخول", breakdown)
        self.assertIn(f"المكافأة المصروفة", breakdown)
        self.assertIn(f"+100 {COIN}", breakdown)

    def test_zero_reward_still_reports_paid_entry_fee(self):
        breakdown = format_wheel_breakdown(0, 50, 0)

        self.assertIn(f"رسوم الدخول", breakdown)
        self.assertIn(f"المكافأة المصروفة:** 0 {COIN}", breakdown)


if __name__ == "__main__":
    unittest.main()
