"""Tests for the per-server ckrcoin emoji."""

import unittest
from types import SimpleNamespace

from cogs.casino.currency_catalog import get_coin_emoji


class FakeEmoji:
    def __init__(self, name, markup, *, available=True):
        self.name = name
        self.markup = markup
        self.available = available

    def __str__(self):
        return self.markup


class CurrencyEmojiTests(unittest.TestCase):
    def test_uses_the_server_emoji_named_ckrcoin(self):
        expected = "<:ckrcoin:123456789012345678>"
        guild = SimpleNamespace(
            emojis=[
                FakeEmoji("other", "<:other:123456789012345679>"),
                FakeEmoji("ckrcoin", expected),
            ]
        )
        self.assertEqual(get_coin_emoji(guild), expected)

    def test_matching_is_case_insensitive(self):
        expected = "<:ckrcoin:123456789012345678>"
        guild = SimpleNamespace(emojis=[FakeEmoji("CKRCOIN", expected)])
        self.assertEqual(get_coin_emoji(guild), expected)

    def test_uses_unicode_fallback_when_the_emoji_is_unavailable(self):
        guild = SimpleNamespace(
            emojis=[
                FakeEmoji(
                    "ckrcoin",
                    "<:ckrcoin:123456789012345678>",
                    available=False,
                )
            ]
        )
        self.assertEqual(get_coin_emoji(guild), "🪙")
        self.assertEqual(get_coin_emoji(None), "🪙")


if __name__ == "__main__":
    unittest.main()
