"""Centralized permission checks for game hosting and bot administration."""
import discord
from discord.ext import commands

import config


def has_admin_access(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def has_staff_access(user) -> bool:
    """Game staff comes only from Administrator or explicitly configured staff roles.

    The per-guild bot-admin allowlist is deliberately not a staff-role shortcut:
    it gates sensitive bot tools, but must never elevate game permissions.
    """
    if has_admin_access(user):
        return True

    role_ids = {
        getattr(role, "id", None)
        for role in getattr(user, "roles", ())
    }
    staff_role_ids = set(getattr(config, "STAFF_ROLE_IDS", ()) or ())
    return bool(role_ids.intersection(staff_role_ids))


def is_staff_or_admin():
    async def predicate(ctx):
        return has_staff_access(ctx.author)

    return commands.check(predicate)
