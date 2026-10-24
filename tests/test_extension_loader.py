"""Tests that the extension loader excludes helper modules as the project is split."""

import unittest

from main import should_load_extension


class ExtensionLoaderClassificationTests(unittest.TestCase):
    def test_game_helpers_are_not_loaded_as_discord_extensions(self):
        helper_modules = {
            "bank.py",
            "bank_ledger.py",
            "bank_schema.py",
            "permissions.py",
            "game_registry.py",
            "game_assets.py",
            "currency_catalog.py",
            "currency_views.py",
            "geoguessr_art.py",
            "geoguessr_storage.py",
            "geoguessr_views.py",
            "mafia_art.py",
            "mafia_views.py",
            "roulette_art.py",
            "roulette_views.py",
        }
        for filename in helper_modules:
            with self.subTest(filename=filename):
                self.assertFalse(should_load_extension("cogs.casino", filename))

    def test_actual_game_cogs_remain_extensions(self):
        for filename in ("mafia.py", "geoguessr.py", "currency.py", "roulette.py", "solo_betting.py", "blackjack.py"):
            with self.subTest(filename=filename):
                self.assertTrue(should_load_extension("cogs.casino", filename))

    def test_utility_allowlist_and_non_python_files_are_respected(self):
        self.assertTrue(should_load_extension("cogs.utility", "help.py"))
        self.assertFalse(should_load_extension("cogs.utility", "bank.py"))
        self.assertFalse(should_load_extension("cogs.casino", "README.md"))
        self.assertFalse(should_load_extension("cogs.casino", "__init__.py"))
        self.assertFalse(should_load_extension("cogs.other", "game.py"))


if __name__ == "__main__":
    unittest.main()
