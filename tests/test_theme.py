"""Regression tests for shared result presentation."""
import unittest

from cogs.utility.theme import THEME, result_embed


class ResultEmbedTests(unittest.TestCase):
    def test_win_uses_shared_brand_palette_and_supports_winner_avatar(self):
        embed = result_embed(
            title="Winner",
            description="Game complete",
            outcome="win",
            thumbnail_url="https://example.com/avatar.png",
        )

        self.assertEqual(embed.color.value, THEME["gold"])
        self.assertEqual(embed.thumbnail.url, "https://example.com/avatar.png")
        self.assertIn("Çukur", embed.footer.text)

    def test_loss_and_unknown_outcomes_have_safe_fallback_colors(self):
        loss = result_embed("Loss", "No winner", outcome="loss")
        unknown = result_embed("Neutral", "No result", outcome="not-a-real-state")

        self.assertEqual(loss.color.value, THEME["red_dark"])
        self.assertEqual(unknown.color.value, THEME["grey"])


if __name__ == "__main__":
    unittest.main()
