"""Regression tests for bot access tiers."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from cogs.casino import bank
from cogs.casino.permissions import (
    ai_enabled_setting_key,
    has_admin_access,
    has_staff_access,
    configured_ai_enabled_for_guild,
)


class FakeUser:
    def __init__(self, user_id=100, roles=(), administrator=False, guild_id=10):
        self.id = user_id
        self.roles = [SimpleNamespace(id=role_id) for role_id in roles]
        self.guild_permissions = SimpleNamespace(administrator=administrator)
        self.guild = SimpleNamespace(id=guild_id)


class PermissionTierTests(unittest.TestCase):
    def setUp(self):
        self.original_owner_ids = config.OWNER_IDS
        self.original_staff_role_ids = config.STAFF_ROLE_IDS
        self.original_guild_admin_check = bank.is_guild_admin
        bank.is_guild_admin = lambda guild_id, user_id: False
        config.OWNER_IDS = {1}
        config.STAFF_ROLE_IDS = [99]

    def tearDown(self):
        config.OWNER_IDS = self.original_owner_ids
        config.STAFF_ROLE_IDS = self.original_staff_role_ids
        bank.is_guild_admin = self.original_guild_admin_check

    def test_configured_bot_owner_id_does_not_bypass_server_permissions(self):
        owner_id = FakeUser(user_id=1)
        self.assertFalse(has_admin_access(owner_id))
        self.assertFalse(has_staff_access(owner_id))

    def test_server_administrator_has_admin_and_staff_access(self):
        admin = FakeUser(user_id=2, administrator=True)
        self.assertTrue(has_admin_access(admin))
        self.assertTrue(has_staff_access(admin))

    def test_configured_staff_role_can_host_games_but_not_manage_admin_settings(self):
        staff = FakeUser(user_id=3, roles=[99])
        self.assertFalse(has_admin_access(staff))
        self.assertTrue(has_staff_access(staff))

    def test_regular_member_has_only_member_access(self):
        member = FakeUser(user_id=4)
        self.assertFalse(has_admin_access(member))
        self.assertFalse(has_staff_access(member))

    def test_database_bot_admin_allowlist_does_not_grant_staff_access(self):
        bank.is_guild_admin = lambda guild_id, user_id: True
        allowlisted_member = FakeUser(user_id=5)
        self.assertFalse(has_admin_access(allowlisted_member))
        self.assertFalse(has_staff_access(allowlisted_member))

    def test_missing_guild_permissions_is_safe(self):
        user = SimpleNamespace(id=5, roles=[])
        self.assertFalse(has_admin_access(user))
        self.assertFalse(has_staff_access(user))

    def test_ai_enablement_uses_the_guild_override_and_falls_back_to_default(self):
        with patch("cogs.casino.permissions.bank.get_setting", return_value=None) as get_setting:
            self.assertTrue(configured_ai_enabled_for_guild(123, default=True))
        get_setting.assert_called_once_with(ai_enabled_setting_key(123))

        with patch("cogs.casino.permissions.bank.get_setting", return_value="false"):
            self.assertFalse(is_ai_enabled_for_guild(123, default=True))

        with patch("cogs.casino.permissions.bank.get_setting", return_value="true"):
            self.assertTrue(is_ai_enabled_for_guild(123, default=False))

    def test_ai_enablement_fails_closed_when_override_cannot_be_read(self):
        with patch(
            "cogs.casino.permissions.bank.get_setting",
            side_effect=OSError("simulated database fault"),
        ):
            self.assertFalse(is_ai_enabled_for_guild(123, default=True))


if __name__ == "__main__":
    unittest.main()
