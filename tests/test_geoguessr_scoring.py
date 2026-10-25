"""Regression tests for GeoGuessr guess-order scoring fairness."""

import unittest

from cogs.casino.geoguessr import order_guesses_by_submission


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
