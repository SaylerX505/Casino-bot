"""Tests for retrying RPS escrow settlement after transient SQLite failures."""
import unittest
from unittest.mock import AsyncMock, patch

from cogs.casino.rps import _settle_wager


class RPSettlementRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_database_errors_are_retried_then_settled(self):
        settled = {"settled": True, "status": "settled", "payout": 190, "tax": 10}
        with (
            patch(
                "cogs.casino.rps.bank.settle_rps_wager",
                side_effect=[RuntimeError("busy"), RuntimeError("busy"), settled],
            ) as settle,
            patch("cogs.casino.rps.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            result = await _settle_wager("game-retry", 101)

        self.assertEqual(result, settled)
        self.assertEqual(settle.call_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_persistent_database_error_fails_closed_after_three_attempts(self):
        with (
            patch("cogs.casino.rps.bank.settle_rps_wager", side_effect=RuntimeError("busy")) as settle,
            patch("cogs.casino.rps.asyncio.sleep", new=AsyncMock()),
        ):
            result = await _settle_wager("game-fails", 101)

        self.assertFalse(result["settled"])
        self.assertEqual(result["reason"], "database_error")
        self.assertEqual(settle.call_count, 3)


if __name__ == "__main__":
    unittest.main()
