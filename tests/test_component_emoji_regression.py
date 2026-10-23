"""Regression tests for Discord component emoji payloads.

Discord.py can serialize arbitrary Unicode strings as button emoji names, but
Discord's API rejects text-only glyphs. Validate the payloads produced by the
Views and keep a source-level guard against the exact class of regression.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

from cogs.casino.blackjack import BlackjackView
from cogs.casino.solo_betting import SoloWagerView

ROOT = Path(__file__).resolve().parents[1]
DISALLOWED_COMPONENT_GLYPHS = {"✕", "🂠", "⏹"}


def component_emojis(view: discord.ui.View) -> dict[str, str | None]:
    """Return each serialized button emoji name keyed by its custom ID."""
    result = {}
    for item in view.walk_children():
        if isinstance(item, discord.ui.Button):
            payload = item.to_component_dict()
            emoji = payload.get("emoji") or {}
            result[item.custom_id] = emoji.get("name")
    return result


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class ComponentEmojiRegressionTests(unittest.TestCase):
    def test_blackjack_waiting_and_playing_payloads_use_supported_emoji(self):
        view = BlackjackView(
            SimpleNamespace(),
            SimpleNamespace(id=101),
            100,
            deck=[],
        )

        self.assertEqual(
            component_emojis(view),
            {
                "blackjack_deal": "🃏",
                "blackjack_cancel": "❌",
            },
        )

        # The playing controls are rendered only after the initial view has
        # been sent. Validate that second payload too, not just the lobby.
        view.phase = "playing"
        view._render_view()
        self.assertEqual(
            component_emojis(view),
            {
                "blackjack_hit": "🎴",
                "blackjack_stand": "🛑",
            },
        )

    def test_solo_wager_cancel_payload_uses_supported_emoji(self):
        view = SoloWagerView(
            SimpleNamespace(),
            SimpleNamespace(id=101),
            100,
        )
        self.assertEqual(
            component_emojis(view)["coinflip_cancel"],
            "❌",
        )

    def test_disallowed_glyphs_are_not_used_as_component_emojis(self):
        """Catch invalid glyphs passed as either keyword or positional arguments."""
        source_root = ROOT / "cogs"
        for path in sorted(source_root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                self.fail(f"Could not inspect component source {path}: {exc}")

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                emoji_values = [
                    keyword.value for keyword in node.keywords if keyword.arg == "emoji"
                ]
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_button"
                    and len(node.args) >= 4
                ):
                    # Blackjack's small helper takes its emoji as arg 4.
                    emoji_values.append(node.args[3])

                for value in emoji_values:
                    glyph = _literal_string(value)
                    if glyph in DISALLOWED_COMPONENT_GLYPHS:
                        self.fail(
                            f"{path.relative_to(ROOT)}:{value.lineno} uses "
                            f"disallowed component glyph {glyph!r}"
                        )


if __name__ == "__main__":
    unittest.main()
