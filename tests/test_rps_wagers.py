"""Regression tests for restart-safe RPS escrow and idempotent settlement."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from cogs.casino import bank


class RPSWagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_file = bank.DB_FILE
        self.previous_connection = bank._conn
        bank.DB_FILE = os.path.join(self.temp_dir.name, "bank.db")
        bank._conn = None
        bank._connect()
        self.assertTrue(bank.credit_balance(101, 1_000)[0])
        self.assertTrue(bank.credit_balance(202, 1_000)[0])

    def tearDown(self):
        connection = bank._conn
        if connection is not None:
            connection.close()
        bank._conn = self.previous_connection
        bank.DB_FILE = self.previous_db_file
        self.temp_dir.cleanup()

    def test_rps_panel_reference_and_status_persist_for_startup_recovery(self):
        self.assertTrue(bank.create_rps_wager("match-panel-ref", 101, 202, 100))
        self.assertTrue(bank.set_rps_wager_message_reference("match-panel-ref", 303, 404))
        records = bank.active_rps_wager_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["game_id"], "match-panel-ref")
        self.assertEqual(records[0]["player_a"], "101")
        self.assertEqual(records[0]["player_b"], "202")
        self.assertEqual(records[0]["amount"], 100)
        self.assertEqual(records[0]["channel_id"], 303)
        self.assertEqual(records[0]["message_id"], 404)
        self.assertEqual(
            bank.rps_wager_status("match-panel-ref"),
            {
                "game_id": "match-panel-ref",
                "player_a": "101",
                "player_b": "202",
                "amount": 100,
                "status": "active",
                "outcome": None,
                "channel_id": 303,
                "message_id": 404,
            },
        )
        self.assertFalse(bank.set_rps_wager_message_reference("missing-game", 303, 404))
        with self.assertRaises(ValueError):
            bank.set_rps_wager_message_reference("match-panel-ref", True, 404)

    def test_challenge_is_not_escrowed_until_the_opponent_accepts(self):
        self.assertTrue(
            bank.create_rps_challenge(
                "challenge-accept", 101, 202, 100, channel_id=303
            )
        )
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)
        self.assertEqual(bank.rps_challenge_status("challenge-accept")["status"], "pending")
        self.assertIsNone(bank.rps_wager_status("challenge-accept"))

        unauthorized = bank.accept_rps_challenge("challenge-accept", 101)
        self.assertFalse(unauthorized["accepted"])
        self.assertEqual(unauthorized["reason"], "not_authorized")
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)

        receipt = bank.accept_rps_challenge("challenge-accept", 202)
        self.assertTrue(receipt["accepted"])
        self.assertFalse(receipt["already_accepted"])
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)
        wager = bank.rps_wager_status("challenge-accept")
        self.assertEqual(wager["status"], "active")
        self.assertEqual(wager["channel_id"], 303)

        repeated = bank.accept_rps_challenge("challenge-accept", 202)
        self.assertTrue(repeated["accepted"])
        self.assertTrue(repeated["already_accepted"])
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)
        stake_entries = bank.get_economy_ledger(
            event_type="rps_wager_stake", reference="challenge-accept"
        )
        self.assertEqual(len(stake_entries), 2)

    def test_decline_closes_invite_without_touching_balances(self):
        self.assertTrue(bank.create_rps_challenge("challenge-decline", 101, 202, 100))
        unauthorized = bank.decline_rps_challenge("challenge-decline", 101)
        self.assertFalse(unauthorized["closed"])
        self.assertEqual(unauthorized["reason"], "not_authorized")

        receipt = bank.decline_rps_challenge("challenge-decline", 202)
        self.assertTrue(receipt["closed"])
        self.assertEqual(receipt["status"], "declined")
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)
        self.assertIsNone(bank.rps_wager_status("challenge-decline"))

    def test_challenger_can_cancel_own_invite_but_not_another_invite(self):
        self.assertTrue(bank.create_rps_challenge("challenge-cancel", 101, 202, 100))
        unauthorized = bank.cancel_rps_challenge("challenge-cancel", 202)
        self.assertFalse(unauthorized["closed"])
        self.assertEqual(unauthorized["reason"], "not_authorized")

        receipt = bank.cancel_rps_challenge("challenge-cancel", 101)
        self.assertTrue(receipt["closed"])
        self.assertEqual(receipt["status"], "cancelled")
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)

    def test_insufficient_funds_on_acceptance_cancels_without_partial_debits(self):
        bank.set_balance(202, 25)
        self.assertTrue(bank.create_rps_challenge("challenge-insufficient", 101, 202, 100))

        receipt = bank.accept_rps_challenge("challenge-insufficient", 202)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["reason"], "insufficient_funds")
        self.assertEqual(receipt["player_a_balance"], 1_000)
        self.assertEqual(receipt["player_b_balance"], 25)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 25)
        self.assertIsNone(bank.rps_wager_status("challenge-insufficient"))
        self.assertEqual(
            bank.rps_challenge_status("challenge-insufficient")["status"],
            "cancelled",
        )

    def test_expiring_pending_challenges_never_refunds_or_debits_currency(self):
        self.assertTrue(bank.create_rps_challenge("challenge-expire-a", 101, 202, 100))
        self.assertTrue(bank.create_rps_challenge("challenge-expire-b", 101, 202, 200))

        expired = bank.expire_pending_rps_challenges()
        self.assertEqual({row["game_id"] for row in expired}, {"challenge-expire-a", "challenge-expire-b"})
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)
        self.assertEqual(bank.expire_pending_rps_challenges(), [])

    def test_concurrent_acceptance_reserves_the_stakes_only_once(self):
        self.assertTrue(bank.create_rps_challenge("challenge-concurrent", 101, 202, 1_000))

        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = list(executor.map(lambda _index: bank.accept_rps_challenge("challenge-concurrent", 202), range(8)))

        self.assertTrue(all(receipt["accepted"] for receipt in receipts))
        self.assertEqual(sum(not receipt["already_accepted"] for receipt in receipts), 1)
        self.assertEqual(bank.get_balance(101), 0)
        self.assertEqual(bank.get_balance(202), 0)
        self.assertEqual(
            len(bank.get_economy_ledger(event_type="rps_wager_stake", reference="challenge-concurrent")),
            2,
        )

    def test_challenge_reference_is_saved_before_or_after_acceptance(self):
        self.assertTrue(bank.create_rps_challenge("challenge-reference", 101, 202, 100, channel_id=303))
        self.assertTrue(bank.set_rps_challenge_message_reference("challenge-reference", 303, 404))
        self.assertEqual(bank.rps_challenge_status("challenge-reference")["message_id"], 404)
        self.assertTrue(bank.accept_rps_challenge("challenge-reference", 202))
        self.assertEqual(bank.rps_wager_status("challenge-reference")["message_id"], 404)

        self.assertTrue(bank.create_rps_challenge("challenge-reference-race", 101, 202, 100, channel_id=303))
        self.assertTrue(bank.accept_rps_challenge("challenge-reference-race", 202)["accepted"])
        self.assertTrue(bank.set_rps_challenge_message_reference("challenge-reference-race", 303, 405))
        self.assertEqual(bank.rps_wager_status("challenge-reference-race")["message_id"], 405)

    def test_winner_settlement_pays_once_and_applies_tax(self):
        self.assertTrue(bank.create_rps_wager("match-1", 101, 202, 100))
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)

        result = bank.settle_rps_wager("match-1", winner_id=101)
        self.assertTrue(result["settled"])
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["payout"], 190)
        self.assertEqual(result["tax"], 10)
        self.assertEqual(bank.get_balance(101), 1_090)
        self.assertEqual(bank.get_balance(202), 900)

        repeated = bank.settle_rps_wager("match-1", winner_id=202)
        self.assertTrue(repeated["already_settled"])
        self.assertEqual(repeated["status"], "settled")
        self.assertEqual(repeated["winner_id"], "101")
        self.assertEqual(repeated["payout"], 190)
        self.assertEqual(repeated["tax"], 10)
        self.assertEqual(bank.get_balance(101), 1_090)
        self.assertEqual(bank.get_balance(202), 900)

    def test_orphaned_wager_is_refunded_once_after_restart(self):
        self.assertTrue(bank.create_rps_wager("match-restart", 101, 202, 100))
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)

        self.assertEqual(bank.refund_orphaned_rps_wagers(), 1)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)
        self.assertEqual(bank.refund_orphaned_rps_wagers(), 0)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)

    def test_insufficient_funds_never_partially_debits(self):
        bank.set_balance(202, 50)

        self.assertFalse(bank.create_rps_wager("match-insufficient", 101, 202, 100))
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 50)

    def test_active_wager_ids_lists_only_unsettled_escrow(self):
        self.assertTrue(bank.create_rps_wager("match-active", 101, 202, 100))
        self.assertEqual(bank.active_rps_wager_ids(), ["match-active"])

        self.assertTrue(bank.settle_rps_wager("match-active")["settled"])
        self.assertEqual(bank.active_rps_wager_ids(), [])

    def test_one_failed_orphan_does_not_block_other_refunds(self):
        self.assertTrue(bank.create_rps_wager("match-fails-recovery", 101, 202, 100))
        self.assertTrue(bank.create_rps_wager("match-recovers", 101, 202, 100))
        real_settle = bank.settle_rps_wager

        def fail_one_record(game_id, winner_id=None):
            if game_id == "match-fails-recovery":
                raise RuntimeError("simulated per-record storage fault")
            return real_settle(game_id, winner_id)

        with patch("cogs.casino.bank.settle_rps_wager", side_effect=fail_one_record):
            self.assertEqual(bank.refund_orphaned_rps_wagers(), 1)

        self.assertEqual(bank.active_rps_wager_ids(), ["match-fails-recovery"])
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)

    def test_economy_reset_is_blocked_while_a_wager_is_active(self):
        self.assertTrue(bank.create_rps_wager("match-reset-guard", 101, 202, 100))

        with self.assertRaisesRegex(RuntimeError, "active RPS wagers"):
            bank.reset_all()

        self.assertEqual(bank.active_rps_wager_ids(), ["match-reset-guard"])
        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)

    def test_concurrent_wager_creation_cannot_overdraw_wallets(self):
        bank.set_balance(101, 1_000)
        bank.set_balance(202, 1_000)

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda index: bank.create_rps_wager(
                        f"concurrent-start-{index}", 101, 202, 1_000
                    ),
                    range(8),
                )
            )

        self.assertEqual(results.count(True), 1)
        self.assertEqual(bank.get_balance(101), 0)
        self.assertEqual(bank.get_balance(202), 0)
        self.assertEqual(len(bank.active_rps_wager_ids()), 1)

    def test_concurrent_settlement_returns_the_original_receipt_once(self):
        self.assertTrue(bank.create_rps_wager("match-concurrent-settle", 101, 202, 100))

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: bank.settle_rps_wager(
                        "match-concurrent-settle", winner_id=101
                    ),
                    range(8),
                )
            )

        self.assertEqual(sum(not result["already_settled"] for result in results), 1)
        self.assertTrue(all(result["settled"] for result in results))
        self.assertTrue(all(result["winner_id"] == "101" for result in results))
        self.assertTrue(all(result["payout"] == 190 for result in results))
        self.assertEqual(bank.get_balance(101), 1_090)
        self.assertEqual(bank.get_balance(202), 900)

    def test_non_participant_cannot_settle_a_wager(self):
        self.assertTrue(bank.create_rps_wager("match-invalid-winner", 101, 202, 100))

        with self.assertRaises(ValueError):
            bank.settle_rps_wager("match-invalid-winner", winner_id=303)

        self.assertEqual(bank.get_balance(101), 900)
        self.assertEqual(bank.get_balance(202), 900)
        self.assertEqual(bank.refund_orphaned_rps_wagers(), 1)
        self.assertEqual(bank.get_balance(101), 1_000)
        self.assertEqual(bank.get_balance(202), 1_000)


if __name__ == "__main__":
    unittest.main()
