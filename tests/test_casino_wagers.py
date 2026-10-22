"""Regression tests for durable solo casino wagers and idempotent settlement."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from cogs.casino import bank


class CasinoWagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_file = bank.DB_FILE
        self.previous_connection = bank._conn
        bank.DB_FILE = os.path.join(self.temp_dir.name, "bank.db")
        bank._conn = None
        bank._connect()
        self.assertTrue(bank.credit_balance(101, 1_000)[0])

    def tearDown(self):
        connection = bank._conn
        if connection is not None:
            connection.close()
        bank._conn = self.previous_connection
        bank.DB_FILE = self.previous_db_file
        self.temp_dir.cleanup()

    def create_wager(self, game_id="coinflip-1", **overrides):
        options = {
            "game_id": game_id,
            "user_id": 101,
            "game_type": "coinflip",
            "amount": 100,
            "choice": "heads",
            "result": "heads",
            "won": True,
            "win_payout": 190,
        }
        options.update(overrides)
        return bank.create_casino_wager(**options)

    def test_winning_wager_debits_once_pays_once_and_writes_ledger(self):
        created = self.create_wager()
        self.assertTrue(created["created"])
        self.assertEqual(bank.get_balance(101), 900)
        first = bank.settle_casino_wager("coinflip-1")
        repeated = bank.settle_casino_wager("coinflip-1")
        self.assertTrue(first["settled"])
        self.assertFalse(first["already_settled"])
        self.assertEqual(first["outcome"], "win")
        self.assertEqual(first["payout"], 190)
        self.assertEqual(first["balance_after"], 1_090)
        self.assertTrue(repeated["already_settled"])
        self.assertEqual(repeated["payout"], 190)
        self.assertEqual(bank.get_balance(101), 1_090)
        stake_entries = bank.get_economy_ledger(event_type="casino_wager_stake", reference="coinflip-1")
        payout_entries = bank.get_economy_ledger(event_type="casino_wager_payout", reference="coinflip-1")
        self.assertEqual(len(stake_entries), 1)
        self.assertEqual(stake_entries[0]["delta"], -100)
        self.assertEqual(len(payout_entries), 1)
        self.assertEqual(payout_entries[0]["delta"], 190)
        self.assertEqual(payout_entries[0]["metadata"]["net_profit"], 90)

    def test_losing_wager_keeps_stake_lost_without_any_payout(self):
        self.create_wager(result="tails", won=False, win_payout=0)
        result = bank.settle_casino_wager("coinflip-1")
        self.assertEqual(result["outcome"], "loss")
        self.assertEqual(result["payout"], 0)
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_economy_ledger(event_type="casino_wager_payout", reference="coinflip-1"), [])
        self.assertEqual(len(bank.get_economy_ledger(event_type="casino_wager_loss", reference="coinflip-1")), 1)

    def test_insufficient_funds_does_not_create_a_wager_or_debit_balance(self):
        bank.set_balance(101, 50)
        result = self.create_wager(amount=100)
        self.assertFalse(result["created"])
        self.assertEqual(result["reason"], "insufficient_funds")
        self.assertEqual(result["balance"], 50)
        self.assertEqual(bank.get_balance(101), 50)
        self.assertEqual(bank.active_casino_wager_ids(), [])

    def test_interrupted_wager_is_refunded_once(self):
        self.create_wager()
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.refund_orphaned_casino_wagers(), 1)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.refund_orphaned_casino_wagers(), 0)
        self.assertEqual(bank.get_balance(101), 1_000)
        result = bank.settle_casino_wager("coinflip-1")
        self.assertTrue(result["already_settled"])
        self.assertEqual(result["status"], "refunded")
        self.assertEqual(result["outcome"], "refund")

    def test_concurrent_settlement_cannot_double_pay(self):
        self.create_wager()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: bank.settle_casino_wager("coinflip-1"), range(8)))
        self.assertTrue(all(result["settled"] for result in results))
        self.assertEqual(sum(not result["already_settled"] for result in results), 1)
        self.assertEqual(sum(result["payout"] for result in results), 190 * 8)
        self.assertEqual(bank.get_balance(101), 1_090)
        self.assertEqual(len(bank.get_economy_ledger(event_type="casino_wager_payout", reference="coinflip-1")), 1)

    def test_duplicate_game_id_cannot_charge_twice(self):
        self.create_wager()
        result = self.create_wager()
        self.assertFalse(result["created"])
        self.assertEqual(result["reason"], "duplicate_game")
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(len(bank.get_economy_ledger(event_type="casino_wager_stake", reference="coinflip-1")), 1)

    def test_wallet_limit_during_payout_refunds_stake_instead_of_overflowing(self):
        self.create_wager(amount=10, win_payout=20)
        bank.set_balance(101, bank.SQLITE_MAX_INT - 10)
        result = bank.settle_casino_wager("coinflip-1")
        self.assertTrue(result["settled"])
        self.assertEqual(result["status"], "refunded")
        self.assertEqual(result["outcome"], "balance_limit")
        self.assertEqual(bank.get_balance(101), bank.SQLITE_MAX_INT)


    def test_multistep_wager_can_commit_final_result_and_push_returns_stake(self):
        self.create_wager(
            game_id="blackjack-push",
            game_type="blackjack",
            choice="blackjack",
            result="in_progress",
            won=False,
            win_payout=0,
        )
        pushed = bank.settle_casino_wager(
            "blackjack-push",
            final_result="player=20,dealer=20",
            final_won=False,
            final_payout=0,
            final_outcome="push",
        )
        repeated = bank.settle_casino_wager("blackjack-push")

        self.assertEqual(pushed["outcome"], "push")
        self.assertEqual(pushed["payout"], 100)
        self.assertEqual(pushed["result"], "player=20,dealer=20")
        self.assertEqual(pushed["balance_after"], 1_000)
        self.assertTrue(repeated["already_settled"])
        self.assertEqual(repeated["outcome"], "push")
        self.assertEqual(repeated["payout"], 100)
        self.assertEqual(bank.get_balance(101), 1_000)
        ledger = bank.get_economy_ledger(event_type="casino_wager_push", reference="blackjack-push")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["delta"], 100)

    def test_multistep_wager_can_settle_final_win_after_the_player_acts(self):
        self.create_wager(
            game_id="blackjack-win",
            game_type="blackjack",
            choice="blackjack",
            result="in_progress",
            won=False,
            win_payout=0,
        )
        result = bank.settle_casino_wager(
            "blackjack-win",
            final_result="player=19,dealer=18",
            final_won=True,
            final_payout=200,
            final_outcome="win",
        )

        self.assertEqual(result["outcome"], "win")
        self.assertEqual(result["result"], "player=19,dealer=18")
        self.assertTrue(result["won"])
        self.assertEqual(result["payout"], 200)
        self.assertEqual(result["balance_after"], 1_100)
        self.assertEqual(bank.get_balance(101), 1_100)

    def test_multistep_wager_rejects_partial_or_invalid_final_state(self):
        self.create_wager(
            game_id="blackjack-invalid",
            game_type="blackjack",
            choice="blackjack",
            result="in_progress",
            won=False,
            win_payout=0,
        )
        with self.assertRaisesRegex(ValueError, "provided together"):
            bank.settle_casino_wager("blackjack-invalid", final_result="final")
        with self.assertRaisesRegex(ValueError, "gross payout"):
            bank.settle_casino_wager(
                "blackjack-invalid",
                final_result="player=21,dealer=18",
                final_won=True,
                final_payout=50,
                final_outcome="win",
            )
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.active_casino_wager_ids(), ["blackjack-invalid"])

    def test_economy_reset_is_blocked_while_solo_wager_is_active(self):
        self.create_wager()
        with self.assertRaisesRegex(RuntimeError, "active casino wagers"):
            bank.reset_all()
        self.assertEqual(bank.active_casino_wager_ids(), ["coinflip-1"])
        self.assertEqual(bank.get_balance(101), 900)

    def test_invalid_stake_and_outcome_types_are_rejected(self):
        for amount in (True, 0, -1, 1.5, "100"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.create_wager(game_id=f"bad-{amount}", amount=amount)
        with self.assertRaises(TypeError):
            self.create_wager(game_id="bad-won", won=1)


if __name__ == "__main__":
    unittest.main()
