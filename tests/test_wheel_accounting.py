"""Regression tests for transparent wheel accounting."""
import unittest

from cogs.casino.wheel import (
    BOX_NET_OUTCOMES,
    COIN,
    FEE,
    SEGMENTS,
    format_wheel_breakdown,
    format_wheel_summary,
    resolve_wheel_segment,
)


class WheelPayoutModelTests(unittest.TestCase):
    def test_positive_sector_numbers_are_net_profit_not_gross_payout(self):
        label, payout, target_loss, _note = resolve_wheel_segment(0)

        self.assertEqual(label, "+20")
        self.assertIsNone(target_loss)
        self.assertEqual(payout, FEE + 20)
        self.assertEqual(payout - FEE, 20)

    def test_multiplier_sector_shows_the_correct_gross_and_net(self):
        label, payout, target_loss, note = resolve_wheel_segment(2)

        self.assertEqual(label, "x2")
        self.assertIsNone(target_loss)
        self.assertEqual(payout, FEE * 2)
        self.assertEqual(payout - FEE, FEE)
        self.assertIn("صافي الربح +50", note)

    def test_zero_sector_refunds_entry_fee(self):
        _label, payout, target_loss, note = resolve_wheel_segment(4)

        self.assertEqual(payout, FEE)
        self.assertIsNone(target_loss)
        self.assertIn("لا ربح ولا خسارة", note)

    def test_box_results_are_bounded_and_settle_to_the_disclosed_net(self):
        for net in BOX_NET_OUTCOMES:
            _label, payout, target_loss, note = resolve_wheel_segment(3, box_net=net)
            self.assertIsNone(target_loss)
            self.assertEqual(payout - FEE, net)
            self.assertIn(f"{net:+,}", note)

    def test_wheel_expected_value_has_a_small_house_edge(self):
        net_outcomes = []
        for index, (_label, outcome) in enumerate(SEGMENTS):
            if outcome == "box":
                net_outcomes.append(sum(BOX_NET_OUTCOMES) / len(BOX_NET_OUTCOMES))
                continue
            _label, payout, target_loss, _note = resolve_wheel_segment(index)
            net_outcomes.append(-target_loss if target_loss is not None else payout - FEE)

        expected_net = sum(net_outcomes) / len(net_outcomes)
        self.assertAlmostEqual(expected_net, -3.75)
        self.assertLess(expected_net, 0)


class WheelSummaryTests(unittest.TestCase):
    def test_final_summary_uses_confirmed_net_delta_not_separate_balance_reads(self):
        player = type("Player", (), {"mention": "<@123>"})()
        summary = format_wheel_summary([(player, "+20", 20)])
        self.assertIn("<@123>", summary)
        self.assertIn("+20", summary)
        self.assertNotIn("+25", summary)

    def test_final_summary_keeps_refused_rounds_out_of_net_profit(self):
        player = type("Player", (), {"mention": "<@123>"})()
        summary = format_wheel_summary([
            (player, None, 0),
            (player, "balance_limit", 0),
            (player, "x2", 50),
        ])
        self.assertIn("رصيد غير كافٍ", summary)
        self.assertIn("حد الرصيد", summary)
        self.assertIn("+50", summary)

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
