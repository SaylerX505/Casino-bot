"""UI regression tests for the single-player coinflip session."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ui import LayoutView

from cogs.casino.coinflip import CoinflipView
from cogs.utility.theme import StatusPanel


class CoinflipViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requester = SimpleNamespace(id=101, mention="<@101>")
        self.cog = SimpleNamespace()
        self.view = CoinflipView(self.cog, self.requester, 100)

    async def asyncTearDown(self):
        self.view.stop()

    def test_coinflip_uses_v2_layout_and_secondary_buttons(self):
        self.assertIsInstance(self.view, LayoutView)
        buttons = [
            item for item in self.view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))
        self.assertEqual({button.label for button in buttons}, {"صورة", "كتابة", "إلغاء"})
        self.assertEqual(len({button.custom_id for button in buttons}), 3)
        self.assertIn("1.9x", self.view._panel_text.content)
        self.assertIn("100", self.view._panel_text.content)

    async def test_session_is_bound_to_the_requesting_member(self):
        other_user = SimpleNamespace(id=202)
        interaction = SimpleNamespace(
            user=other_user,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        allowed = await self.view.interaction_check(interaction)

        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], StatusPanel)

    async def test_session_owner_can_pass_interaction_check(self):
        interaction = SimpleNamespace(
            user=self.requester,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertTrue(await self.view.interaction_check(interaction))
        interaction.response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
