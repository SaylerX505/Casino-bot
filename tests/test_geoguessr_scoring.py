"""Regression tests for GeoGuessr guess-order scoring fairness."""

import unittest
from types import SimpleNamespace

from cogs.casino.geoguessr import SERVER_LOGO_URL, build_final_result, order_guesses_by_submission


class GeoGuessrFinalResultTests(unittest.TestCase):
    def player(self, user_id):
        return SimpleNamespace(
            id=user_id,
            mention=f"<@{user_id}>",
            display_avatar=SimpleNamespace(url=f"https://example.com/{user_id}.png"),
        )

    def test_zero_score_game_is_not_announced_as_a_tie(self):
        first = self.player(101)
        second = self.player(202)

        description, image_url, outcome = build_final_result({first: 0, second: 0})

        self.assertIn("مفيش فائز بالنقاط", description)
        self.assertNotIn("تعادل", description)
        self.assertEqual(image_url, SERVER_LOGO_URL)
        self.assertEqual(outcome, "neutral")

    def test_positive_equal_scores_are_announced_as_a_true_tie(self):
        first = self.player(101)
        second = self.player(202)

        description, image_url, outcome = build_final_result({first: 10, second: 10})

        self.assertIn("تعادل", description)
        self.assertIn(first.mention, description)
        self.assertIn(second.mention, description)
        self.assertEqual(outcome, "neutral")

    def test_single_positive_top_score_has_one_winner(self):
        first = self.player(101)
        second = self.player(202)

        description, image_url, outcome = build_final_result({first: 10, second: 5})

        self.assertIn(first.mention, description)
        self.assertNotIn(second.mention, description)
        self.assertEqual(image_url, first.display_avatar.url)
        self.assertEqual(outcome, "success")

    def test_empty_scoreboard_is_handled_safely(self):
        description, _image_url, outcome = build_final_result({})

        self.assertIn("مفيش فائز", description)
        self.assertEqual(outcome, "neutral")


class GeoGuessrScoringOrderTests(unittest.TestCase):
    def test_first_bonus_follows_submission_time_not_processing_completion(self):
        submitted_first = object()
        processed_first = object()
        completion_order = [processed_first, submitted_first]
        received_at = {submitted_first: 10.0, processed_first: 11.0}

        self.assertEqual(
            order_guesses_by_submission(completion_order, received_at),
            [submitted_first, processed_first],
        )

    def test_missing_timestamp_sorts_after_known_submissions(self):
        submitted = object()
        missing = object()
        self.assertEqual(
            order_guesses_by_submission([missing, submitted], {submitted: 3.0}),
            [submitted, missing],
        )


if __name__ == "__main__":
    unittest.main()
