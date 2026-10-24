"""Repository-wide guardrails for the Components V2 message policy."""
# ruff: noqa: I001

import ast
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COGS = os.path.join(ROOT, "cogs")
MESSAGE_METHODS = {"send", "send_message", "reply", "edit", "edit_original_response"}


def _direct_embed_sends():
    """Find Discord send/edit calls that pass the legacy embed keyword directly."""
    findings = []
    for directory, _subdirectories, filenames in os.walk(COGS):
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, "r", encoding="utf-8") as source_file:
                    tree = ast.parse(source_file.read(), filename=path)
            except (OSError, SyntaxError) as error:
                relative_path = os.path.relpath(path, ROOT).replace(os.sep, "/")
                findings.append((relative_path, 1, f"could not inspect source: {type(error).__name__}"))
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in MESSAGE_METHODS:
                    continue
                if any(keyword.arg == "embed" for keyword in node.keywords):
                    relative_path = os.path.relpath(path, ROOT).replace(os.sep, "/")
                    findings.append(
                        (
                            relative_path,
                            node.lineno,
                            f"{node.func.attr}(embed=...) bypasses the Components V2 renderer",
                        )
                    )
    return sorted(findings)


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
