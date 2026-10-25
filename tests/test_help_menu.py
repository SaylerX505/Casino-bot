"""Regression tests for a help menu that does not describe itself."""

import unittest

import config
from cogs.utility.help import HELP_PAGES, SLASH_COMMANDS, format_help_command


class HelpMenuTests(unittest.TestCase):
    def test_commands_menu_is_not_listed_inside_its_own_member_page(self):
        fields = HELP_PAGES["member"]["fields"]
        labels = [label for label, _description in fields]
        self.assertNotIn("مساعدة / help", labels)
        self.assertNotIn("commands", SLASH_COMMANDS)
        self.assertNotIn("commands", [key.casefold() for key in SLASH_COMMANDS])

        rendered_source = "\n".join(
            f"{label}\n{description}"
            for label, description in fields
        ).casefold()
        self.assertNotIn("/commands", rendered_source)

    def test_commands_with_arguments_still_show_their_slash_forms(self):
        cases = {
            "بروفايل [@عضو]": "/بروفايل",
            "استخدم صندوق أو تذكرة": "/استخدم",
            "حومص 100 @عضو": "/حومص",
            "عملة 100": "/coinflip",
            "نرد 100": "/dice",
            "سلوت 100": "/slots",
            "بلاكجاك 100": "/blackjack",
        }
        for label, slash_name in cases.items():
            with self.subTest(label=label):
                rendered = format_help_command(label)
                self.assertIn(slash_name, rendered)
                self.assertIn(config.PREFIX + label, rendered)

    def test_every_documented_command_has_a_prefix_form(self):
        for page in HELP_PAGES.values():
            for label, _description in page["fields"]:
                with self.subTest(label=label):
                    self.assertIn(config.PREFIX, format_help_command(label))

    def test_member_page_keeps_the_actual_member_commands(self):
        labels = {label for label, _description in HELP_PAGES["member"]["fields"]}
        self.assertIn("رصيد / bal", labels)
        self.assertIn("عملة 100", labels)
        self.assertIn("نرد 100", labels)
        self.assertIn("بلاكجاك 100", labels)


if __name__ == "__main__":
    unittest.main()
