"""Privacy and inventory rollback tests for the Mafia scan consumable."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.casino.mafia import Mafia


def view_text(view):
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


class MafiaScanPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.author = SimpleNamespace(
            id=101,
            display_name="Player One",
            mention="<@101>",
            send=AsyncMock(),
        )
        self.mafia_target = SimpleNamespace(
            id=202,
            display_name="Mafia Member",
            mention="<@202>",
        )
        self.currency = SimpleNamespace(
            get_item_count=Mock(return_value=1),
            remove_item=Mock(return_value=True),
            add_item=Mock(return_value=None),
        )
        self.game = SimpleNamespace(
            game_name="mafia",
            alive=[self.author, self.mafia_target],
            mafias=[self.mafia_target],
        )
        self.bot = SimpleNamespace(
            get_cog=Mock(return_value=self.currency),
            active_games={77: {"game_ref": self.game}},
        )
        self.cog = Mafia(self.bot)
        self.ctx = SimpleNamespace(
            author=self.author,
            channel=SimpleNamespace(id=77),
            interaction=None,
            send=AsyncMock(),
        )

    async def run_scan(self):
        await Mafia.mafia_scan.callback(self.cog, self.ctx)

    async def test_success_sends_secret_result_only_to_dm(self):
        await self.run_scan()

        self.author.send.assert_awaited_once()
        private_kwargs = self.author.send.await_args.kwargs
        self.assertIsInstance(private_kwargs["view"], discord.ui.LayoutView)
        self.assertFalse(private_kwargs["allowed_mentions"].users)
        self.currency.remove_item.assert_called_once_with(101, "mafia_scan", 1)
        self.currency.add_item.assert_not_called()

        public_text = view_text(self.ctx.send.await_args.kwargs["view"])
        self.assertIn("النتيجة اتبعتت في الخاص", public_text)
        self.assertNotIn("<@202>", public_text)

    async def test_dm_failure_refunds_item_and_never_exposes_hint(self):
        response = SimpleNamespace(status=403, reason="Forbidden")
        self.author.send.side_effect = discord.Forbidden(response, "DMs disabled")

        await self.run_scan()

        self.currency.add_item.assert_called_once_with(101, "mafia_scan", 1)
        public_text = view_text(self.ctx.send.await_args.kwargs["view"])
        self.assertIn("رجّعنا الأداة", public_text)
        self.assertNotIn("<@202>", public_text)

    async def test_refund_failure_is_reported_without_claiming_item_was_returned(self):
        response = SimpleNamespace(status=403, reason="Forbidden")
        self.author.send.side_effect = discord.Forbidden(response, "DMs disabled")
        self.currency.add_item.side_effect = OSError("simulated storage failure")

        await self.run_scan()

        public_text = view_text(self.ctx.send.await_args.kwargs["view"])
        self.assertIn("تعذر استرداد الأداة", public_text)
        self.assertNotIn("رجّعنا الأداة لشنطتك", public_text)
        self.assertNotIn("<@202>", public_text)


if __name__ == "__main__":
    unittest.main()
