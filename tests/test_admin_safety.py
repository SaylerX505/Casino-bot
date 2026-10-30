"""Security regression tests for Discord and bot-specific administration."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.casino import bank
from cogs.utility.admin import AdminCommands


def fake_interaction(*, user_id=10, guild_id=123, administrator=True, roles=()):
    return SimpleNamespace(
        guild=object(),
        guild_id=guild_id,
        user=SimpleNamespace(
            id=user_id,
            guild=SimpleNamespace(id=guild_id),
            roles=[SimpleNamespace(id=role_id) for role_id in roles],
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

    async def test_economy_credit_allows_a_wizard_admin_role_without_discord_admin(self):
        interaction = fake_interaction(administrator=False, roles=(55,))
        member = SimpleNamespace(id=20, mention="<@20>")

        with (
            patch("cogs.casino.permissions.configured_manager_role_ids", return_value={55}),
            patch.object(bank, "credit_balance", return_value=(True, 100)) as credit,
            patch.object(bank, "is_guild_admin") as bot_admin_check,
        ):
            await self.cog.economy_credit.callback(self.cog, interaction, member, 100)

        credit.assert_called_once_with(20, 100)
        bot_admin_check.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].children[0].content.split("\\n", 1)[0], "# تمت إضافة العملات")

    async def test_economy_debit_allows_a_wizard_admin_role_without_discord_admin(self):
        interaction = fake_interaction(administrator=False, roles=(56,))
        member = SimpleNamespace(id=20, mention="<@20>")

        with (
            patch("cogs.casino.permissions.configured_manager_role_ids", return_value={56}),
            patch.object(bank, "remove_balance", return_value=(True, 0)) as remove,
            patch.object(bank, "is_guild_admin") as bot_admin_check,
        ):
            await self.cog.economy_debit.callback(self.cog, interaction, member, 100)

        remove.assert_called_once_with(20, 100)
        bot_admin_check.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].children[0].content.split("\\n", 1)[0], "# تم خصم العملات")

    async def test_economy_management_rejects_roles_not_configured_in_the_wizard(self):
        interaction = fake_interaction(administrator=False, roles=(77,))
        member = SimpleNamespace(id=20, mention="<@20>")

        with (
            patch("cogs.casino.permissions.configured_manager_role_ids", return_value={55}),
            patch.object(bank, "credit_balance") as credit,
        ):
            await self.cog.economy_credit.callback(self.cog, interaction, member, 100)

        credit.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].children[0].content.split("\\n", 1)[0], "# غير مسموح")

    async def test_economy_commands_are_not_hidden_by_discord_administrator_default(self):
        self.assertIsNone(AdminCommands.admin.default_permissions)

    async def test_ai_toggle_is_owner_only_while_setting_is_global(self):
        interaction = fake_interaction()
        self.cog.bot.is_owner = AsyncMock(return_value=False)

        with (
            patch.object(self.cog, "_require_admin", new=AsyncMock(return_value=True)),
        ):
            await self.cog.ai_toggle.callback(self.cog, interaction, False)

        self.cog.bot.get_cog.assert_not_called()
        interaction.response.send_message.assert_awaited_once()

    async def test_global_status_is_not_visible_to_server_admins(self):
        interaction = fake_interaction()
        self.cog.bot.is_owner = AsyncMock(return_value=False)

        with (
            patch.object(self.cog, "_require_admin", new=AsyncMock(return_value=True)),
            patch.object(bank, "list_guild_admins") as list_admins,
        ):
            await self.cog.admin_status.callback(self.cog, interaction)

        list_admins.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(
            view.children[0].children[0].content.split("\n", 1)[0],
            "# الإعداد ده مش متاح لأدمن السيرفر",
        )

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
