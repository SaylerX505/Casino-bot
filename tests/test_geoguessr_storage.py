"""Persistence safety tests for GeoGuessr's generated image tracker."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from cogs.casino import geoguessr


class GeoGuessrStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.tracker_path = os.path.join(self.temp_dir.name, "used_images.json")
        self.database_path = os.path.join(self.temp_dir.name, "database.json")

    def test_tracker_write_is_atomic_and_filters_invalid_values(self):
        with patch.object(geoguessr, "USED_TRACKER_FILE", self.tracker_path):
            geoguessr.save_used_images({"z-image", "a-image", 12, None})

        with open(self.tracker_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), ["a-image", "z-image"])
        self.assertFalse(os.path.exists(f"{self.tracker_path}.tmp"))

    def test_corrupt_tracker_fails_safe_without_crashing_module(self):
        with open(self.tracker_path, "w", encoding="utf-8") as handle:
            handle.write("{ broken")

        with patch.object(geoguessr, "USED_TRACKER_FILE", self.tracker_path):
            self.assertEqual(geoguessr.load_used_images(), set())

    def test_corrupt_static_database_is_reported_as_unavailable(self):
        with open(self.database_path, "w", encoding="utf-8") as handle:
            handle.write("{ broken")

        with patch.object(geoguessr, "DATABASE_FILE", self.database_path):
            self.assertEqual(geoguessr.load_database(), {})


if __name__ == "__main__":
    unittest.main()
