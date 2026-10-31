"""Repository-wide guardrails for the Components V2 message policy."""
# ruff: noqa: I001

import ast
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COGS = os.path.join(ROOT, "cogs")
MESSAGE_METHODS = {"send", "send_message", "reply", "edit", "edit_original_response"}


def _direct_embed_sends():
    """Find outgoing message calls that pass legacy embed payloads directly."""
    findings = []
    sources = [os.path.join(ROOT, "main.py")]
    for directory, _subdirectories, filenames in os.walk(COGS):
        sources.extend(
            os.path.join(directory, filename)
            for filename in filenames
            if filename.endswith(".py")
        )

    for path in sorted(sources):
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
            for keyword in node.keywords:
                if keyword.arg == "embed":
                    legacy_payload = True
                elif keyword.arg == "embeds":
                    # Clearing prior embeds when replacing a message with a V2
                    # view is valid; sending any legacy embeds is not.
                    is_empty_list = isinstance(keyword.value, ast.List) and not keyword.value.elts
                    legacy_payload = not (
                        is_empty_list
                        and node.func.attr in {"edit", "edit_original_response"}
                    )
                else:
                    continue

                if legacy_payload:
                    relative_path = os.path.relpath(path, ROOT).replace(os.sep, "/")
                    findings.append(
                        (
                            relative_path,
                            node.lineno,
                            f"{node.func.attr}({keyword.arg}=...) bypasses the Components V2 renderer",
                        )
                    )
                    break
    return sorted(findings)


def _promotional_button_labels():
    findings = []
    for directory, _subdirectories, filenames in os.walk(COGS):
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, "r", encoding="utf-8") as source_file:
                    source = source_file.read()
            except OSError as error:
                relative_path = os.path.relpath(path, ROOT).replace(os.sep, "/")
                findings.append((relative_path, 1, f"could not inspect source: {type(error).__name__}"))
                continue
            for marker in ("Developed by @neglawy", "Developed by @neglawy & @17 Nicotine"):
                if marker in source:
                    relative_path = os.path.relpath(path, ROOT).replace(os.sep, "/")
                    line = source[:source.index(marker)].count("\n") + 1
                    findings.append((relative_path, line, f"legacy promotional label remains: {marker}"))
    return sorted(findings)


def _mention_policy_is_safe():
    """Check that the client suppresses broad pings while RPS opts in to its invitee."""
    try:
        with open(os.path.join(ROOT, "main.py"), "r", encoding="utf-8") as source_file:
            main_tree = ast.parse(source_file.read(), filename="main.py")
        with open(os.path.join(COGS, "casino", "rps.py"), "r", encoding="utf-8") as source_file:
            rps_tree = ast.parse(source_file.read(), filename="cogs/casino/rps.py")
    except (OSError, SyntaxError):
        return False, False

    has_safe_default = False
    for node in ast.walk(main_tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "Bot":
            continue
        for keyword in node.keywords:
            value = keyword.value
            if (
                keyword.arg == "allowed_mentions"
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "none"
                and isinstance(value.func.value, ast.Attribute)
                and value.func.value.attr == "AllowedMentions"
            ):
                has_safe_default = True

    challenge_invite_can_ping_only_target = False
    for node in ast.walk(rps_tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "send":
            continue
        for keyword in node.keywords:
            value = keyword.value
            if keyword.arg != "allowed_mentions" or not isinstance(value, ast.Call):
                continue
            names = {item.arg: item.value for item in value.keywords}
            if (
                "users" in names
                and isinstance(names.get("everyone"), ast.Constant)
                and names["everyone"].value is False
                and isinstance(names.get("roles"), ast.Constant)
                and names["roles"].value is False
            ):
                challenge_invite_can_ping_only_target = True
    return has_safe_default, challenge_invite_can_ping_only_target


class ComponentsV2PolicyTests(unittest.TestCase):
    def test_no_game_or_utility_message_sends_legacy_embed_keyword(self):
        findings = _direct_embed_sends()
        self.assertEqual(
            findings,
            [],
            "All sent or edited embeds must be converted to Components V2:\n"
            + "\n".join(f"{path}:{line}: {reason}" for path, line, reason in findings),
        )

    def test_game_ui_does_not_add_disabled_developer_promo_buttons(self):
        findings = _promotional_button_labels()
        self.assertEqual(
            findings,
            [],
            "Remove legacy promotional UI labels:\n"
            + "\n".join(f"{path}:{line}: {reason}" for path, line, reason in findings),
        )


    def test_mentions_are_silent_by_default_with_explicit_rps_invitation_opt_in(self):
        safe_default, controlled_invite = _mention_policy_is_safe()
        self.assertTrue(safe_default, "The bot must not ping users in routine game recaps.")
        self.assertTrue(controlled_invite, "RPS challenge should explicitly notify only the invited opponent.")


if __name__ == "__main__":
    unittest.main()
