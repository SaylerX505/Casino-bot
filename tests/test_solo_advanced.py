"""Regression tests for the new interactive and instant solo betting games."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.casino import solo_advanced, solo_betting
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
                image = solo_betting._render_advanced_instant_art(game_type)
                self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


class NumberRouletteConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requester = SimpleNamespace(id=123456)
        choices = {str(number): {"label": str(number), "emoji": None} for number in range(37)}
        self.view = solo_betting.SoloWagerView(
            None,
            self.requester,
            100,
            game_type="number_roulette",
            game_title="روليت الأرقام",
            choices=choices,
            payout_numerator=3600,
            odds_description="1 من 37",
            instructions="اختار رقمًا ثم أكّد.",
            payout_description="36x إجمالي الصرف.",
            context=None,
        )
        self.interaction = SimpleNamespace(
            user=self.requester,
            response=SimpleNamespace(
                edit_message=AsyncMock(),
                send_message=AsyncMock(),
            ),
        )

    async def test_selecting_number_does_not_submit_wager_until_confirmed(self):
        self.assertIsNone(self.view.selected_number)
        self.assertTrue(self.view.confirm_button.disabled)

        await self.view.select_roulette_number(self.interaction, "17")

        self.assertEqual(self.view.selected_number, "17")
        self.assertFalse(self.view.confirm_button.disabled)
        self.interaction.response.edit_message.assert_awaited_once_with(view=self.view)

        self.view.choose = AsyncMock()
        await self.view.confirm_roulette_number(self.interaction)
        self.view.choose.assert_awaited_once_with(self.interaction, side="17")

    async def test_invalid_number_cannot_enable_confirmation(self):
        await self.view.select_roulette_number(self.interaction, "37")

        self.assertIsNone(self.view.selected_number)
        self.assertTrue(self.view.confirm_button.disabled)
        self.interaction.response.send_message.assert_awaited_once()


class _TestInteractiveWagerView(solo_advanced.InteractiveSoloWagerView):
    def _render_art(self):
        return b"test-art"


class InteractiveWagerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_escrow_render_failure_requests_refund(self):
        message = SimpleNamespace(id=98765, edit=AsyncMock(return_value=None))
        context = SimpleNamespace(
            author=SimpleNamespace(id=12345),
            channel=SimpleNamespace(id=54321),
            send=AsyncMock(return_value=message),
        )
        view = _TestInteractiveWagerView(
            None,
            context,
            100,
            game_type="test",
            game_title="Test",
            accent=0x333333,
        )
        refresh = view._refresh
        refresh_calls = 0

        def fail_after_escrow_once():
            nonlocal refresh_calls
            refresh_calls += 1
            if refresh_calls == 2:
                raise RuntimeError("simulated artwork/render failure after escrow")
            refresh()

        view._refresh = fail_after_escrow_once
        refund_receipt = {
            "settled": True,
            "already_settled": False,
            "status": "refunded",
            "outcome": "refund",
            "payout": 0,
            "balance_after": 1_000,
            "result": "pending",
        }

        with (
            patch.object(
                solo_advanced.bank,
                "create_casino_wager",
                return_value={"created": True, "balance": 900},
            ),
            patch.object(
                solo_advanced.bank,
                "settle_casino_wager",
                return_value=refund_receipt,
            ) as settle,
        ):
            started = await view.start()
            self.assertFalse(started)
            self.assertIsNotNone(view._settlement_task)
            await view._settlement_task

        self.assertTrue(view.done)
        settle.assert_called_once_with(view.game_id, force_refund=True)



if __name__ == "__main__":
    unittest.main()
