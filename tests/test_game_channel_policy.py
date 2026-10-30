"""Tests for per-guild game-channel policy and configurable staff roles."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.casino import permissions


class GameChannelPolicyTests(unittest.TestCase):
    def test_missing_global_policy_defaults_to_all_channels(self):
        with patch("cogs.casino.permissions.bank.get_setting", return_value="all"):
            self.assertTrue(permissions.is_game_channel_allowed(100, 200, "mafia"))
            self.assertTrue(permissions.is_game_channel_allowed(100, 201, "mines"))

    def test_one_selected_channel_applies_to_solo_and_group_games(self):
        with patch(
            "cogs.casino.permissions.bank.get_setting",
            return_value="200",
        ):
            for game_key in ("mafia", "roulette", "mines", "crash", "plinko", "number_roulette", "hilo"):
                with self.subTest(game_key=game_key):
                    self.assertTrue(permissions.is_game_channel_allowed(100, 200, game_key))
                    self.assertFalse(permissions.is_game_channel_allowed(100, 201, game_key))

    def test_global_channel_setting_is_scoped_per_guild(self):
        def setting(key, default=None):
            return {
                "guild:100:games_channel": "200",
                "guild:101:games_channel": "300",
            }.get(key, default)

        with patch("cogs.casino.permissions.bank.get_setting", side_effect=setting):
            self.assertTrue(permissions.is_game_channel_allowed(100, 200, "mafia"))
            self.assertFalse(permissions.is_game_channel_allowed(100, 300, "mafia"))
            self.assertTrue(permissions.is_game_channel_allowed(101, 300, "mines"))
            self.assertFalse(permissions.is_game_channel_allowed(101, 200, "mines"))

    def test_malformed_global_channel_setting_fails_closed(self):
        with patch("cogs.casino.permissions.bank.get_setting", return_value="not-a-channel"):
            self.assertFalse(permissions.is_game_channel_allowed(100, 200, "mafia"))

    def test_database_failure_fails_closed_for_global_channel_policy(self):
        with patch(
            "cogs.casino.permissions.bank.get_setting",
            side_effect=RuntimeError("database unavailable"),
        ):
            self.assertFalse(permissions.is_game_channel_allowed(100, 200, "mines"))

    def test_game_name_aliases_resolve_to_one_persisted_key(self):
        cases = {
            "مافيا": "mafia",
            "mafia": "mafia",
            "روليت": "roulette",
            "hide-and-seek": "hide_and_seek",
            "hideandseek": "hide_and_seek",
            "hide_seek": "hide_and_seek",
            "hide": "hide_and_seek",
            "غميضه": "hide_and_seek",
            "جيو": "geoguessr",
            "maf": "mafia",
            "rr": "roulette",
            "russianroulette": "roulette",
            "musicalchairs": "chairs",
            "pitwheel": "wheel",
            "rock-paper-scissors": "rps",
            "rockpaperscissors": "rps",
            "quiz": "riddle",
            "كراسي": "chairs",
            "chairs": "chairs",
            "musical-chairs": "chairs",
            "roulette": "roulette",
            "عجلة": "wheel",
            "حومص": "rps",
            "فزورة": "riddle",
            "coinflip": "coinflip",
            "flip": "coinflip",
            "cf": "coinflip",
            "coin": "coinflip",
            "coin-flip": "coinflip",
            "عملة": "coinflip",
            "dice": "dice",
            "roll": "dice",
            "die": "dice",
            "نرد": "dice",
            "زهر": "dice",
            "slots": "slots",
            "spin": "slots",
            "slot": "slots",
            "سلوت": "slots",
            "blackjack": "blackjack",
            "black-jack": "blackjack",
            "bj": "blackjack",
            "mine": "mines",
            "nr": "number_roulette",
            "hilow": "hilo",
            "بلاكجاك": "blackjack",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(permissions.game_key_from_command_name(name), expected)

    def test_blackjack_slash_command_uses_its_configured_game_key(self):
        interaction = SimpleNamespace(
            command=SimpleNamespace(name="blackjack", qualified_name="blackjack"),
            data={"name": "blackjack", "options": [{"name": "bet", "type": 3, "value": "100"}]},
        )
        self.assertEqual(permissions.game_key_from_interaction(interaction), "blackjack")

    def test_nested_slash_game_command_is_resolved(self):
        interaction = SimpleNamespace(
            command=SimpleNamespace(name="games", qualified_name="games mafia"),
            data={"name": "games", "options": [{"name": "mafia", "type": 1}]},
        )
        self.assertEqual(permissions.game_key_from_interaction(interaction), "mafia")

    def test_staff_roles_override_legacy_default_after_wizard_save(self):
        guild = SimpleNamespace(id=100)
        member = SimpleNamespace(
            guild=guild,
            guild_permissions=SimpleNamespace(administrator=False),
            roles=[SimpleNamespace(id=20)],
        )
        with patch("cogs.casino.permissions.bank.get_setting", return_value=json.dumps([20])):
            self.assertTrue(permissions.has_staff_access(member))
        with patch("cogs.casino.permissions.bank.get_setting", return_value="[]"):
            self.assertFalse(permissions.has_staff_access(member))

    def test_administrator_remains_an_explicit_override(self):
        member = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            guild_permissions=SimpleNamespace(administrator=True),
            roles=[],
        )
        with patch("cogs.casino.permissions.bank.get_setting", side_effect=RuntimeError):
            self.assertTrue(permissions.has_staff_access(member))


if __name__ == "__main__":
    unittest.main()
