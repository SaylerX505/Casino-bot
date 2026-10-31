"""Regression tests for the optional disabled credit on terminal game panels."""

import unittest

import discord

from cogs.utility.theme import (
    StatusPanel,
    add_developer_credit,
    base_embed,
    embed_to_v2,
)


CREDIT_LABEL = "Developed by Sayler & Neglawy"


def credit_buttons(view):
    return [
        item for item in view.walk_children()
        if isinstance(item, discord.ui.Button) and item.label == CREDIT_LABEL
    ]


class DeveloperCreditTests(unittest.TestCase):
    def test_credit_is_disabled_and_appears_on_requested_result_panels(self):
        views = (
            StatusPanel("انتهت اللعبة", "النتيجة", "success", developer_credit=True),
            embed_to_v2(base_embed("انتهت اللعبة", "النتيجة"), developer_credit=True),
        )
        for view in views:
            with self.subTest(view=type(view).__name__):
                buttons = credit_buttons(view)
                self.assertEqual(len(buttons), 1)
                self.assertTrue(buttons[0].disabled)
                self.assertEqual(buttons[0].label, CREDIT_LABEL)

    def test_credit_is_not_added_to_ordinary_status_panels(self):
        self.assertEqual(credit_buttons(StatusPanel("تنبيه", "رسالة")), [])

    def test_fallback_helper_is_idempotent(self):
        view = StatusPanel("انتهت اللعبة", "النتيجة")
        self.assertIs(add_developer_credit(view), view)
        self.assertIs(add_developer_credit(view), view)
        buttons = credit_buttons(view)
        self.assertEqual(len(buttons), 1)
        self.assertTrue(buttons[0].disabled)


if __name__ == "__main__":
    unittest.main()
