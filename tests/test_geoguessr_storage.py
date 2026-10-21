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

    def test_round_selection_excludes_invalid_records_and_missing_images(self):
        image_dir = os.path.join(self.temp_dir.name, "images")
        os.makedirs(image_dir, exist_ok=True)
        records = {}
        for index in range(6):
            filename = f"{index + 1}.jpg"
            with open(os.path.join(image_dir, filename), "wb") as handle:
                handle.write(b"image")
            records[f"valid-{index}"] = {
                "country_code": "us",
                "country_ar": "الولايات المتحدة",
                "coords": [40.0, -73.0],
                "filename": filename,
            }

        records["missing-file"] = {
            "country_code": "fr",
            "country_ar": "فرنسا",
            "coords": [48.8, 2.3],
            "filename": "missing.jpg",
        }
        records["unsafe-path"] = {
            "country_code": "gb",
            "country_ar": "بريطانيا",
            "coords": [51.5, -0.1],
            "filename": "../secret.jpg",
        }
        records["invalid-coordinates"] = {
            "country_code": "de",
            "country_ar": "ألمانيا",
            "coords": [120, 13],
            "filename": "1.jpg",
        }

        with (
            patch.object(geoguessr, "game_db", records),
            patch.object(geoguessr, "IMAGES_DIR", image_dir),
        ):
            self.assertEqual(
                geoguessr.valid_geo_round_keys(),
                [f"valid-{index}" for index in range(6)],
            )


if __name__ == "__main__":
    unittest.main()
