"""Privacy and isolation regression tests for the AI responder."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.utility.ai import AI, MAX_HISTORY_MESSAGES


def make_message(guild_id=100, channel_id=200, user_id=300, roles=()):
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id, owner_id=999),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(id=user_id, roles=list(roles), premium_since=None),
    )


class AIPrivacyTests(unittest.TestCase):
    def setUp(self):
        with patch("cogs.utility.ai.bank.get_setting", return_value="true"):
            self.ai = AI(SimpleNamespace(user=SimpleNamespace(id=500)))

    def test_conversation_history_is_isolated_by_guild_channel_and_user(self):
        first = self.ai._conversation_key(make_message(guild_id=100, channel_id=200, user_id=300))
        other_channel = self.ai._conversation_key(make_message(guild_id=100, channel_id=201, user_id=300))
        other_guild = self.ai._conversation_key(make_message(guild_id=101, channel_id=200, user_id=300))
        other_user = self.ai._conversation_key(make_message(guild_id=100, channel_id=200, user_id=301))
        self.assertEqual(len({first, other_channel, other_guild, other_user}), 4)

    def test_payload_includes_only_requesting_users_saved_conversation(self):
        current = make_message(guild_id=100, channel_id=200, user_id=300)
        key = self.ai._conversation_key(current)
        other_key = self.ai._conversation_key(make_message(guild_id=100, channel_id=200, user_id=301))
        self.ai._remember_exchange(other_key, "private message from another person", "private reply")
        self.ai._remember_exchange(key, "my previous question", "my previous answer")

        payload = self.ai._build_payload_messages(current, key, "new question")
        content = "\n".join(item["content"] for item in payload)
        self.assertIn("my previous question", content)
        self.assertIn("new question", content)
        self.assertNotIn("private message from another person", content)
        self.assertNotIn("private reply", content)
        self.assertNotIn("[سياق الشات حالياً]", content)

    def test_history_has_a_fixed_upper_bound(self):
        key = self.ai._conversation_key(make_message())
        for index in range(MAX_HISTORY_MESSAGES + 5):
            self.ai._remember_exchange(key, f"question-{index}", f"answer-{index}")
        self.assertEqual(len(self.ai._history[key]), MAX_HISTORY_MESSAGES)
        self.assertIn(f"question-{MAX_HISTORY_MESSAGES + 4}", self.ai._history[key][-2]["content"])

    def test_role_allowlist_is_read_from_configuration(self):
        member = make_message(roles=[SimpleNamespace(id=12345)])
        with patch("cogs.utility.ai.config.AI_ALLOWED_ROLE_IDS", {12345}):
            self.assertTrue(self.ai._is_authorized(member))
        with patch("cogs.utility.ai.config.AI_ALLOWED_ROLE_IDS", {54321}):
            self.assertFalse(self.ai._is_authorized(member))


if __name__ == "__main__":
    unittest.main()
