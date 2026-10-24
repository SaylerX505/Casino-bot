"""Public lobby access must stay separate from configured admin controls."""

import unittest

from cogs.casino.chairs import Chairs
from cogs.casino.geoguessr import GeoGuessr
from cogs.casino.hide_and_seek import HideAndSeek
from cogs.casino.mafia import Mafia
from cogs.casino.roulette import Roulette
from cogs.casino.wheel import Wheel
from cogs.utility.help import HELP_PAGES, SLASH_COMMANDS


class PublicGameLobbyPolicyTests(unittest.TestCase):
    def test_members_can_start_all_public_group_game_lobbies(self):
        public_lobbies = (
            Mafia.start_mafia,
            Roulette.start_roulette,
            HideAndSeek.start_hide_seek,
            GeoGuessr.start_geo,
            Chairs.start_chairs,
            Wheel.wheel,
        )

        for command in public_lobbies:
            with self.subTest(command=command.qualified_name):
                self.assertEqual(
                    command.checks,
                    [],
                    f"{command.qualified_name} should not require a staff role to host a game lobby",
                )

    def test_public_help_lists_group_games(self):
        member_labels = {
            label for label, _description in HELP_PAGES["member"]["fields"]
        }
        for label in ("مافيا", "روليت", "غميضه", "جيو / geo", "كراسي", "عجلة / wheel"):
            with self.subTest(label=label):
                self.assertIn(label, member_labels)

    def test_staff_help_keeps_admin_controls_separate(self):
        staff_labels = {
            label for label, _description in HELP_PAGES["staff"]["fields"]
        }
        self.assertIn("ايقاف / stop", staff_labels)
        self.assertIn("wizard / إعداد", staff_labels)
        self.assertIn("admin / إدارة", staff_labels)
        self.assertNotIn("مافيا", staff_labels)
        self.assertEqual(SLASH_COMMANDS["wizard"], "wizard")
        self.assertEqual(SLASH_COMMANDS["admin"], "admin")


if __name__ == "__main__":
    unittest.main()
