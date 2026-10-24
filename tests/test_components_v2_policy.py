"""Repository-wide guardrails for the Components V2 message policy."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
COGS = ROOT / "cogs"
MESSAGE_METHODS = {"send", "send_message", "reply", "edit", "edit_original_response"}


def _direct_embed_sends():
    """Find Discord send/edit calls that pass the legacy embed keyword directly."""
    findings = []
    for path in sorted(COGS.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as error:
            findings.append((path.relative_to(ROOT).as_posix(), 1, f"could not inspect source: {type(error).__name__}"))
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in MESSAGE_METHODS:
                continue
            if any(keyword.arg == "embed" for keyword in node.keywords):
                findings.append(
                    (
                        path.relative_to(ROOT).as_posix(),
                        node.lineno,
                        f"{node.func.attr}(embed=...) bypasses the Components V2 renderer",
                    )
                )
    return findings


class ComponentsV2PolicyTests(unittest.TestCase):
    def test_no_game_or_utility_message_sends_legacy_embed_keyword(self):
        findings = _direct_embed_sends()
        self.assertEqual(
            findings,
            [],
            "All sent or edited embeds must be converted to Components V2:\n"
            + "\n".join(f"{path}:{line}: {reason}" for path, line, reason in findings),
        )


if __name__ == "__main__":
    unittest.main()
