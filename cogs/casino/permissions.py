"""Shared, centralized permission checks for the bot's command access tiers."""
import discord
from discord.ext import commands

import config


def is_bot_owner(user) -> bool:
    """Whether this user is configured as a bot owner."""
    return getattr(user, "id", None) in getattr(config, "OWNER_IDS", set())


def has_admin_access(user) -> bool:
    """Bot owners and Discord server administrators can manage server-level bot settings."""
    if is_bot_owner(user):
        return True
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


def is_admin_or_owner():
    """Prefix-command check for owner/admin-only configuration commands."""
    async def predicate(ctx):
        if has_admin_access(ctx.author):
            return True

        embed = discord.Embed(
            description="❌ الأمر ده مخصص للي معاهم صلاحية Administrator في السيرفر.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, ephemeral=ctx.interaction is not None)
        return False

    return commands.check(predicate)


def is_staff_or_owner():
    """Prefix-command check for staff-hosted games."""
    async def predicate(ctx):
        if has_staff_access(ctx.author):
            return True

        embed = discord.Embed(
            description="❌ الأمر ده مخصص للستاف أو أدمن السيرفر أو أدمنز البوت المصرح لهم.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, ephemeral=ctx.interaction is not None)
        return False

    return commands.check(predicate)
