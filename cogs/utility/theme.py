# --- EMBED THEME ---
"""موحد شكل البوت - الوان الحفرة + helpers"""
import discord
import config

THEME = {
    "gold": 0xF5C542,      # فوز / تتويج
    "red_dark": 0x8B1E1E,  # خطر / طرد / فخ
    "teal": 0x2AA6A4,      # لوبي / انتظار
    "grey": 0x393A41,      # خلفية / محايد
    "green": 0x2ECC71,     # صح / كسب
    "orange": 0xE67E22,    # تحذير / ضريبة
}

LOGO_URL = getattr(config, "SERVER_LOGO_URL", "")
BRAND = getattr(config, "BRAND", "Çukur")
PREFIX = getattr(config, "PREFIX", "+")


class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Developed by @neglawy", style=discord.ButtonStyle.secondary, disabled=True))


def base_embed(title: str = None, description: str = None, color: int = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color or THEME["grey"])
    if LOGO_URL:
        e.set_thumbnail(url=LOGO_URL)
        e.set_footer(text=f"كازينو الحفرة • {BRAND} • Developed by @neglawy", icon_url=LOGO_URL)
    else:
        e.set_footer(text=f"كازينو الحفرة • {BRAND} • Developed by @neglawy")
    return e


def result_embed(
    title: str,
    description: str,
    outcome: str = "neutral",
    thumbnail_url: str = None,
) -> discord.Embed:
    colors = {
        "win": THEME["gold"],
        "success": THEME["green"],
        "loss": THEME["red_dark"],
        "warning": THEME["orange"],
        "neutral": THEME["grey"],
    }
    embed = base_embed(title=title, description=description, color=colors.get(outcome, THEME["grey"]))
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return embed

def lobby_embed(title: str, rules: str, players, time_left: int, max_players: int = 25) -> discord.Embed:
    if players:
        plist = "\n".join([f"**{i+1}** : {p.mention}" for i, p in enumerate(players)])
    else:
        plist = "في انتظار اللاعبين... خش يا معلم يلا 🏃"
    e = discord.Embed(
        title=title,
        description=f"{rules}\n**اللاعبين المنضمين ({len(players)}/{max_players}):**\n{plist}\n\n⏳ تبدأ بعد **{time_left}** ثانية...",
        color=THEME["teal"],
    )
    if LOGO_URL:
        e.set_thumbnail(url=LOGO_URL)
        e.set_footer(text=f"كازينو الحفرة • {BRAND}", icon_url=LOGO_URL)
    return e


def is_staff_or_owner():
    from discord.ext import commands

    async def predicate(ctx):
        if ctx.author.id in config.OWNER_IDS:
            return True
        user_role_ids = [r.id for r in getattr(ctx.author, "roles", [])]
        if any(rid in config.STAFF_ROLE_IDS for rid in user_role_ids):
            return True
        if getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator:
            return True
        e = discord.Embed(description="❌ الألعاب دي مخصصة للستاف والأونرز بس!", color=THEME["red_dark"])
        await ctx.send(embed=e)
        return False

    from discord.ext import commands as _c
    return _c.check(predicate)


def get_currency(bot):
    try:
        return bot.get_cog("Currency")
    except Exception:
        return None
