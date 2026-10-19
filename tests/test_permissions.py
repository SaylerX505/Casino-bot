"""Regression tests for the command permission tiers."""
import unittest
from types import SimpleNamespace

import config
from cogs.casino.permissions import (
    has_admin_access,
    has_staff_access,
    is_bot_owner,
)


class FakeUser:
    def __init__(self, user_id=100, roles=(), administrator=False):
        self.id = user_id
        self.roles = [SimpleNamespace(id=role_id) for role_id in roles]
        self.guild_permissions = SimpleNamespace(administrator=administrator)


class PermissionTierTests(unittest.TestCase):
    def setUp(self):
        self.original_owner_ids = config.OWNER_IDS
        self.original_staff_role_ids = config.STAFF_ROLE_IDS
        config.OWNER_IDS = {1}
        config.STAFF_ROLE_IDS = [99]

    def tearDown(self):
        config.OWNER_IDS = self.original_owner_ids
        config.STAFF_ROLE_IDS = self.original_staff_role_ids

    def test_bot_owner_has_all_access_tiers(self):
        owner = FakeUser(user_id=1)
        self.assertTrue(is_bot_owner(owner))
        self.assertTrue(has_admin_access(owner))
        self.assertTrue(has_staff_access(owner))

    def test_server_administrator_has_admin_and_staff_access(self):
        admin = FakeUser(user_id=2, administrator=True)
        self.assertFalse(is_bot_owner(admin))
        self.assertTrue(has_admin_access(admin))
        self.assertTrue(has_staff_access(admin))

    def test_configured_staff_role_can_host_games_but_not_administer_bot(self):
        staff = FakeUser(user_id=3, roles=[99])
        self.assertFalse(is_bot_owner(staff))
        self.assertFalse(has_admin_access(staff))
        self.assertTrue(has_staff_access(staff))

    def test_regular_member_has_only_member_access(self):
        member = FakeUser(user_id=4)
        self.assertFalse(is_bot_owner(member))
        self.assertFalse(has_admin_access(member))
        self.assertFalse(has_staff_access(member))

    def test_missing_guild_permissions_is_safe(self):
        user = SimpleNamespace(id=5, roles=[])
        self.assertFalse(has_admin_access(user))
        self.assertFalse(has_staff_access(user))


if __name__ == "__main__":
    unittest.main()
