"""Tests for per-guild game-channel policy and configurable staff roles."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.casino import permissions


class GameChannelPolicyTests(unittest.TestCase):
    def test_missing_policy_defaults_to_all_channels(self):
        with patch("cogs.casino.permissions.bank.get_setting", return_value="all"):
            self.assertTrue(permissions.is_game_channel_allowed(100, 200, "mafia"))
            self.assertTrue(permissions.is_game_channel_allowed(100, 201, "mafia"))

    def test_configured_channel_allowlist_is_enforced(self):
        with patch(
            "cogs.casino.permissions.bank.get_setting",
            return_value=json.dumps([200, 201]),
        ):
            self.assertTrue(permissions.is_game_channel_allowed(100, 200, "mafia"))
            self.assertTrue(permissions.is_game_channel_allowed(100, 201, "mafia"))
            self.assertFalse(permissions.is_game_channel_allowed(100, 202, "mafia"))

    def test_empty_channel_allowlist_disables_a_game(self):
        with patch("cogs.casino.permissions.bank.get_setting", return_value="[]"):
            self.assertFalse(permissions.is_game_channel_allowed(100, 200, "mafia"))
            self.assertFalse(permissions.is_game_channel_allowed(100, 999, "mafia"))

    def test_malformed_policy_fails_closed(self):
        with patch("cogs.casino.permissions.bank.get_setting", return_value="not-json"):
            self.assertFalse(permissions.is_game_channel_allowed(100, 200, "mafia"))

    def test_channel_policies_are_scoped_per_game_and_guild(self):
        def setting(key, default=None):
            values = {
                "guild:100:game_channels:mafia": json.dumps([200]),
                "guild:101:game_channels:mafia": json.dumps([300]),
                "guild:100:game_channels:roulette": "all",
            }
            return values.get(key, default)

        with patch("cogs.casino.permissions.bank.get_setting", side_effect=setting):
            self.assertTrue(permissions.is_game_channel_allowed(100, 200, "mafia"))
            self.assertFalse(permissions.is_game_channel_allowed(100, 300, "mafia"))
            self.assertTrue(permissions.is_game_channel_allowed(101, 300, "mafia"))
            self.assertTrue(permissions.is_game_channel_allowed(100, 999, "roulette"))
            self.assertTrue(permissions.is_game_channel_allowed(100, 999, "coinflip"))
            self.assertTrue(permissions.is_game_channel_allowed(100, 999, "dice"))

    def test_game_name_aliases_resolve_to_one_persisted_key(self):
        cases = {
            "مافيا": "mafia",
            "mafia": "mafia",
            "روليت": "roulette",
            "hide-and-seek": "hide_and_seek",
            "غميضه": "hide_and_seek",
            "جيو": "geoguessr",
            "كراسي": "chairs",
            "عجلة": "wheel",
            "حومص": "rps",
            "فزورة": "riddle",
            "coinflip": "coinflip",
            "flip": "coinflip",
            "عملة": "coinflip",
            "dice": "dice",
            "نرد": "dice",
            "زهر": "dice",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(permissions.game_key_from_command_name(name), expected)

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
