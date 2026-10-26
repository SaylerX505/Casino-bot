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
        self.assertIn("يبدأ تلقائيًا خلال **17 ثانية**", text)
        self.assertNotIn("لو العدد وصل للحد الأدنى", text)
        self.assertLess(len(text), 280)
        self.assertIsInstance(embed_to_v2(panel), LayoutView)

    def test_long_rosters_are_capped_without_losing_the_total_count(self):
        players = [SimpleNamespace(mention=f"<@{user_id}>") for user_id in range(200, 208)]
        panel = lobby_embed(
            "لعبة تجريبية",
            "قواعد مختصرة.",
            players,
            5,
            host=self.host,
            min_players=1,
        )

        self.assertIn("اللاعبون (8/25)", panel.description)
        for user_id in range(200, 205):
            self.assertIn(f"<@{user_id}>", panel.description)
        self.assertNotIn("<@205>", panel.description)
        self.assertIn("+3 لاعب آخر", panel.description)

    def test_multiline_rules_become_a_single_compact_summary(self):
        panel = lobby_embed(
            "لعبة تجريبية",
            "السطر الأول.\nالسطر الثاني.",
            [],
            12,
            min_players=1,
        )

        self.assertTrue(panel.description.startswith("السطر الأول. السطر الثاني."))


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
