"""Security regression tests for Discord and bot-specific administration."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.casino import bank
from cogs.utility.admin import AdminCommands


def fake_interaction(*, user_id=10, guild_id=123, administrator=True):
    return SimpleNamespace(
        guild=object(),
        guild_id=guild_id,
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(administrator=administrator),
        ),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


class AdminSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = AdminCommands(Mock())

    async def test_access_add_rejects_member_without_discord_administrator(self):
        interaction = fake_interaction()
        member = SimpleNamespace(
            id=20,
            bot=False,
            mention="<@20>",
            guild_permissions=SimpleNamespace(administrator=False),
        )

        with patch.object(bank, "add_guild_admin") as add_admin:
            await self.cog.access_add.callback(self.cog, interaction, member)

        add_admin.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].children[0].content.split("\n", 1)[0], "# الصلاحية غير موجودة")

    async def test_access_add_allows_only_a_real_server_administrator(self):
        interaction = fake_interaction()
        member = SimpleNamespace(
            id=20,
            bot=False,
            mention="<@20>",
            guild_permissions=SimpleNamespace(administrator=True),
        )

        with patch.object(bank, "add_guild_admin", return_value=True) as add_admin:
            await self.cog.access_add.callback(self.cog, interaction, member)

        add_admin.assert_called_once_with(123, 20)
        interaction.response.send_message.assert_awaited_once()

    async def test_bank_reset_is_blocked_without_deleting_shared_economy(self):
        interaction = fake_interaction()

        with (
            patch.object(bank, "is_guild_admin", return_value=True),
            patch.object(bank, "backup_database") as backup_database,
            patch.object(bank, "reset_all") as reset_all,
        ):
            await self.cog.bank_reset.callback(self.cog, interaction)

        backup_database.assert_not_called()
        reset_all.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].children[0].content.split("\n", 1)[0], "# التصفير موقوف لحماية البيانات")


if __name__ == "__main__":
    unittest.main()
