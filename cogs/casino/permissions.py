"""Shared, centralized permission checks for the bot's command access tiers."""
import discord
from discord.ext import commands

import config


def has_admin_access(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def has_staff_access(user) -> bool:
    if has_admin_access(user):
        return True

    guild = getattr(user, "guild", None)
    if guild is not None:
        try:
            from cogs.casino import bank
            if bank.is_guild_admin(guild.id, user.id):
                return True
        except Exception:
            pass

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
