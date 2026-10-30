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
    def test_blackjack_buttons_have_no_emoji(self):
        view = BlackjackView(
            SimpleNamespace(),
            SimpleNamespace(id=101),
            100,
            deck=[],
        )
        self.assertEqual(
            component_emojis(view),
            {"blackjack_deal": None, "blackjack_cancel": None},
        )
        view.phase = "playing"
        view._render_view()
        self.assertEqual(
            component_emojis(view),
            {"blackjack_hit": None, "blackjack_stand": None},
        )

    def test_solo_game_buttons_have_no_emoji(self):
        view = SoloWagerView(SimpleNamespace(), SimpleNamespace(id=101), 100)
        self.assertIsNone(component_emojis(view)["coinflip_cancel"])

    def test_button_constructors_do_not_set_emojis(self):
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
                is_button = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Button"
                )
                if is_button and any(keyword.arg == "emoji" for keyword in node.keywords):
                    self.fail(
                        f"{path.relative_to(ROOT)}:{node.lineno} assigns an emoji to a button"
                    )


if __name__ == "__main__":
    unittest.main()
