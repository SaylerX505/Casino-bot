import discord
from discord.ext import commands
import sys
import os
import asyncio
import config

def t(text: str) -> str:
    return f".ᐟ {text} .ᐟ"

def str_to_partial_emoji(emoji_str: str):
    """Convert '<:name:id>' or '<a:name:id>' to discord.PartialEmoji"""
    if not emoji_str.startswith('<'):
        return emoji_str  # return as is (unicode emoji)
    
    animated = emoji_str.startswith('<a:')
    parts = emoji_str.strip('<>').split(':')
    if len(parts) >= 3:
        name = parts[1]
        emoji_id = int(parts[2])
        return discord.PartialEmoji(name=name, id=emoji_id, animated=animated)
    return emoji_str

class RestartConfirmView(discord.ui.View):
    def __init__(self, bot, requester):
        super().__init__(timeout=30)
        self.bot = bot
        self.requester = requester

    @discord.ui.button(label="Confirm Restart", style=discord.ButtonStyle.green, emoji=str_to_partial_emoji(config.EMOJIS.get('check', '✅')))
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.requester:
            return await interaction.response.send_message(t("You don't have permission to use this button!"), ephemeral=True)
        
        await interaction.response.defer()
        embed = discord.Embed(
            title="<:loading6667:1512806374395084940> |  Restarting Bot...",
            description="Bot will be unavailable for 10-20 seconds.",
            color=config.EMBED_COLOR
        )
        embed.set_footer(text=config.BRAND)
        await interaction.edit_original_response(embed=embed, view=None)
        
        print(f"[RESTART] Bot restarted by {interaction.user.name}")
        await self.bot.close()
        os.execv(sys.executable, ['python'] + sys.argv)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey, emoji=str_to_partial_emoji(config.EMOJIS.get('cross', '❌')))
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.requester:
            return await interaction.response.send_message(t("You don't have permission!"), ephemeral=True)
        
        embed = discord.Embed(title="<:cross:1512824131232465076> | Cancelled", description="Restart cancelled.", color=config.EMBED_COLOR)
        embed.set_footer(text=config.BRAND)
        await interaction.response.edit_message(embed=embed, view=None)

class AdminTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="Testing")
    @commands.is_owner()
    async def test(self, ctx):
        """Test command for bot owner"""
        await ctx.send(embed=discord.Embed(description=t("This is a test command."), color=0x393a41))

    @commands.command(name="rc")
    @commands.is_owner()
    async def rc(self, ctx, cog_name: str = None):
        if not cog_name:
            return await ctx.reply(embed=discord.Embed(
                description=t("Please specify a cog name. Example: `+reload geoguessr`"),
                color=config.EMBED_COLOR
            ))
        
        # Possible paths where the cog might live
        possible_paths = [
            f'cogs.casino.{cog_name}',
            f'cogs.utility.{cog_name}',
            f'cogs.{cog_name}'
        ]
        
        for path in possible_paths:
            if path in self.bot.extensions:
                try:
                    await self.bot.reload_extension(path)
                    await ctx.reply(embed=discord.Embed(
                        description=t(f"<:check:1511996429630181458> Reloaded `{path}`"),
                        color=config.EMBED_COLOR
                    ))
                    return
                except Exception as e:
                    await ctx.reply(embed=discord.Embed(
                        description=t(f"<:cross:1512824131232465076> Failed to reload `{path}`:\n`{e}`"),
                        color=config.EMBED_COLOR
                    ))
                    return
        
        await ctx.reply(embed=discord.Embed(
            description=t(f"<:cross:1512824131232465076> Cog `{cog_name}` not found. Make sure you use the correct filename (without .py)."),
            color=config.EMBED_COLOR
        ))

    @commands.command(name="rs")
    @commands.is_owner()
    async def rs(self, ctx):
        """Sync slash commands (reload slash commands)"""
        try:
            synced = await self.bot.tree.sync()
            await ctx.reply(embed=discord.Embed(
                description=t(f"<:check:1511996429630181458> Synced {len(synced)} slash command(s)."),
                color=config.EMBED_COLOR
            ))
        except Exception as e:
            await ctx.reply(embed=discord.Embed(
                description=t(f"<:cross:1512824131232465076> Failed to sync slash commands: {e}"),
                color=config.EMBED_COLOR
            ))

    @commands.command(name="restart")
    @commands.is_owner()
    async def restart_bot(self, ctx):
        """Restart the bot completely (Bot Owner only)"""
        embed = discord.Embed(
            title="<:check:1511996429630181458> | Confirm Restart",
            description="Are you sure you want to restart the bot?\nBot will be unavailable for 10-20 seconds.",
            color=config.EMBED_COLOR
        )
        embed.set_footer(text=config.BRAND)
        view = RestartConfirmView(self.bot, ctx.author)
        await ctx.reply(embed=embed, view=view)

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_commands(self, ctx):
        """Sync slash commands manually (Bot Owner only)"""
        embed = discord.Embed(description=t("<:loading6667:1512806374395084940> | Syncing slash commands..."), color=config.EMBED_COLOR)
        msg = await ctx.reply(embed=embed)
        
        try:
            synced = await self.bot.tree.sync()
            result_embed = discord.Embed(
                description=t(f"<:check:1511996429630181458> | Synced {len(synced)} slash command(s)!"),
                color=config.EMBED_COLOR
            )
            result_embed.set_footer(text=config.BRAND)
            await msg.edit(embed=result_embed)
        except Exception as e:
            await msg.edit(embed=discord.Embed(description=t(f"<:cross:1512824131232465076> | Failed: {e}"), color=config.EMBED_COLOR))

async def setup(bot):
    await bot.add_cog(AdminTools(bot))