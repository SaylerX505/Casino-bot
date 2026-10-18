"""Shared checks for commands intended for server staff and bot owners."""
import discord
from discord.ext import commands

import config


def is_staff_or_owner():
    """Return a prefix-command check for trusted game hosts."""
    async def predicate(ctx):
        if ctx.author.id in config.OWNER_IDS:
            return True

        role_ids = {role.id for role in getattr(ctx.author, "roles", ())}
        if role_ids.intersection(config.STAFF_ROLE_IDS):
            return True

        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return True

        embed = discord.Embed(
            description="❌ الألعاب دي مخصصة للستاف والأونرز بس!",
            color=discord.Color.red(),
        )
        # Prefix-command contexts cannot send ephemeral channel messages.
        await ctx.send(embed=embed)
        return False

    return commands.check(predicate)
