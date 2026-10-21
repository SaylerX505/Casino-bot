import discord
from discord.ext import commands
from discord.ui import View, Select
import random
import config
from cogs.casino.game_registry import release_game_task

def t(text: str) -> str:
    return f".ᐟ {text} .ᐟ"

class HelpSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Casino & Economy", value="casino", emoji="🎰", description="Games, shop, and money commands"),
            discord.SelectOption(label="Admin Tools", value="admin", emoji="⚙️", description="Bot refresh/restart (owner only)"),
        ]
        super().__init__(placeholder=".ᐟ Select Category .ᐟ", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        pages = {
            "casino": {
                "title": "🎰 Casino & Economy",
                "fields": [
                    (f"{config.PREFIX}مافيا", "Start a Mafia game (5‑25 players)"),
                    (f"{config.PREFIX}روليت", "Start Russian Roulette"),
                    (f"{config.PREFIX}غميضه", "Start a visual Hide & Seek game (2-25 players)"),
                    (f"{config.PREFIX}جيو / {config.PREFIX}geo", "Start a GeoGuessr game (location guessing)"),
                    (f"{config.PREFIX}كراسي", "Play Musical Chairs"),
                    (f"{config.PREFIX}عجلة", "لف عجلة الحفرة واكسب لحد x2 (ستاف)"),
                    (f"{config.PREFIX}حومص 100 @واحد", "تحدي حجرة ورقة مقص بمراهنة"),
                    (f"{config.PREFIX}فزورة", "فزورة مصرية - اول واحد يجاوب يكسب 30"),
                    (f"{config.PREFIX}رصيد / {config.PREFIX}bal", "Open the Economy Hub (Daily, Transfer, Top, Shop)"),
                    (f"{config.PREFIX}شنطتي / {config.PREFIX}inv", "Check your inventory for bought items"),
                ]
            },
            "admin": {
                "title": "⚙️ Admin Tools (Bot Owner Only)",
                "fields": [
                    (f"{config.PREFIX}stop", "Instantly stop an active game in the channel"),
                    (f"{config.PREFIX}refresh", "Reload all cogs and sync slash commands"),
                    (f"{config.PREFIX}restart", "Restart the bot completely"),
                    (f"{config.PREFIX}sync", "Manually sync slash commands"),
                ]
            }
        }

        d = pages[self.values[0]]
        embed = discord.Embed(title=f"└ {d['title']}", color=config.EMBED_COLOR)
        for name, desc in d["fields"]:
            if name: embed.add_field(name=f"`{name}`", value=f"╰ {desc}", inline=False)
            else: embed.add_field(name="\u200b", value=f"*{desc}*", inline=False)
        embed.set_footer(text=f".ᐟ Prefix : {config.PREFIX} • {config.BRAND} .ᐟ")
        embed.set_thumbnail(url=config.SERVER_LOGO_URL)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class HelpView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HelpSelect())

class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help", aliases=["مساعدة", "h"])
    async def custom_help(self, ctx):
        """Show the help menu"""
        lat = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title=".ᐟ Help Center .ᐟ",
            description=f"Select a category from the menu below.\nPrefix: **{config.PREFIX}**\n\n{config.EMOJIS.get('ping', '🏓')} Latency: {lat}ms",
            color=config.EMBED_COLOR
        )
        if hasattr(config, 'RANDOM_IMAGES') and config.RANDOM_IMAGES:
            embed.set_image(url=random.choice(config.RANDOM_IMAGES))
        embed.set_footer(text=config.BRAND)
        embed.set_thumbnail(url=config.SERVER_LOGO_URL)
        await ctx.send(embed=embed, view=HelpView())

    # ==========================================
    #  أمر الإيقاف الإضطراري الشامل
    # ==========================================
    @commands.command(name="stop", aliases=["ايقاف", "انهاء"])
    async def stop_game(self, ctx):
        """إيقاف أي لعبة شغالة في الروم"""
        if not hasattr(self.bot, 'active_games') or ctx.channel.id not in self.bot.active_games:
            return await ctx.send("❌ مفيش لعبة شغالة هنا عشان أوقفها!")
        
        game_info = self.bot.active_games[ctx.channel.id]
        
        ADMIN_IDS = set(config.OWNER_IDS)  # bot owners can always stop; admins via guild_permissions below
        is_admin = ctx.author.id in ADMIN_IDS or ctx.author.guild_permissions.administrator
        is_host = ctx.author.id == game_info['host_id']
        
        if not (is_admin or is_host):
            return await ctx.send("❌ للأدمنز أو صاحب اللعبة فقط تقدر توقفها!")
            
        game_info['task'].cancel()
        view = game_info.get("view")
        if view is not None:
            for child in view.children:
                child.disabled = True
            message = getattr(view, "message", None)
            if message is not None:
                try:
                    await message.edit(view=view)
                except discord.HTTPException:
                    pass
        release_game_task(self.bot, ctx.channel.id, game_info['task'])
        
        embed = discord.Embed(
            title="🛑 إيقاف إضطراري", 
            description=f"تم إنهاء لعبة **{game_info['name']}** فوراً بواسطة {ctx.author.mention}!", 
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(HelpCommand(bot))