"""Regression tests for transparent wheel accounting."""
import unittest

from cogs.casino.wheel import COIN, FEE, format_wheel_breakdown


class WheelAccountingTests(unittest.TestCase):
    def test_full_loss_is_reported_once_as_the_final_loss(self):
        breakdown = format_wheel_breakdown(0, 50, 0)

        self.assertIn(f"**الخسارة النهائية شاملة رسوم الدخول:** −50 {COIN}", breakdown)
        self.assertNotIn(f"**رسوم الدخول:** −{FEE:,} {COIN}", breakdown)
        self.assertNotIn("الخصم الإضافي", breakdown)

    def test_partial_loss_explains_refund_without_counting_it_twice(self):
        breakdown = format_wheel_breakdown(0, 30, 20)

        self.assertIn(f"**الخسارة النهائية شاملة رسوم الدخول:** −30 {COIN}", breakdown)
        self.assertIn(f"اترد 20 من رسوم الدخول بالفعل", breakdown)
        self.assertNotIn("المردود من رسوم الدخول:** +20", breakdown)

    def test_reward_shows_entry_fee_and_gross_payout(self):
        breakdown = format_wheel_breakdown(100, None, 0)

        self.assertIn(f"رسوم الدخول:** −{FEE:,} {COIN}", breakdown)
        self.assertIn(f"المكافأة المصروفة:** +100 {COIN}", breakdown)
        self.assertNotIn("الخسارة النهائية", breakdown)

    def test_zero_reward_on_a_non_loss_path_still_reports_fee(self):
        breakdown = format_wheel_breakdown(0, None, 0)

        self.assertIn(f"رسوم الدخول:** −{FEE:,} {COIN}", breakdown)
        self.assertIn(f"المكافأة المصروفة:** 0 {COIN}", breakdown)


if __name__ == "__main__":
    unittest.main()
