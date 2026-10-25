"""Regression tests for consistent public game-lobby copy."""

import unittest
from types import SimpleNamespace

from discord.ui import LayoutView

from cogs.utility.theme import embed_to_v2, lobby_embed


class LobbyUXTests(unittest.TestCase):
    def setUp(self):
        self.host = SimpleNamespace(display_name="LobbyHost")
        self.player = SimpleNamespace(mention="<@200>")

    def test_lobby_surfaces_host_minimum_capacity_and_countdown(self):
        panel = lobby_embed(
            "لعبة تجريبية",
            "قواعد اللعبة هنا.",
            [self.player],
            17,
            max_players=12,
            host=self.host,
            min_players=5,
        )
        text = panel.description
        self.assertIn("المضيف:", text)
        self.assertIn("LobbyHost", text)
        self.assertIn("الحد الأدنى للبدء:** 5 لاعب", text)
        self.assertIn("اللاعبون (1/12)", text)
        self.assertIn("الوقت المتبقي:** 17 ثانية", text)
        self.assertIn("لو العدد وصل للحد الأدنى", text)
        self.assertIsInstance(embed_to_v2(panel), LayoutView)

    def test_solo_capable_lobby_does_not_claim_it_needs_two_players(self):
        panel = lobby_embed(
            "عجلة الحفرة",
            "ادخل ولف.",
            [],
            10,
            host=self.host,
            min_players=1,
        )
        self.assertNotIn("الحد الأدنى للبدء", panel.description)
        self.assertIn("مفيش لاعبين لسه", panel.description)
        self.assertNotIn("خش يا معلم", panel.description)


if __name__ == "__main__":
    unittest.main()
