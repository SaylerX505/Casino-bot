import discord
from discord.ext import commands
import asyncio
import random
import json
import os

try:
    from cogs.utility.theme import THEME, WatermarkView, base_embed, get_currency
except ImportError:
    from utility.theme import THEME, WatermarkView, base_embed, get_currency

COIN_EMOJI = "<:ckrcoin:1513743032124575784>"
DATA_FILE = "data/fazoora.json"
REWARD = 30


def load_bank():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("فزورة", [])
    except Exception:
        return [{"q": "حاجة كل ما تاخد منها تكبر؟", "a": ["الحفرة"]}]


def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    for a, b in [("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")]:
        s = s.replace(a, b)
    return s


class Fazoora(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bank = load_bank()

    @commands.hybrid_command(name="فزورة", aliases=["فزوره", "fazoora", "احزر"], description="ابدأ فزورة جماعية")
    async def fazoora(self, ctx):
        if hasattr(self.bot, "active_games") and ctx.channel.id in self.bot.active_games:
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة شغالة هنا! استنى تخلص الاول.", color=THEME["red_dark"]))
        item = random.choice(self.bank)
        q, answers = item["q"], [normalize(x) for x in item["a"]]
        e = discord.Embed(title="🧠 فزورة الحفرة", description=f"**{q}**\n\n⏳ معاكم **30 ثانية**! اول واحد يجاوب صح يكسب **{REWARD}** {COIN_EMOJI}\nاكتب الاجابة في الشات على طول 👇", color=THEME["teal"])
        msg = await ctx.send(embed=e)

        def check(m):
            return m.channel.id == ctx.channel.id and not m.author.bot and normalize(m.content) in answers

        try:
            win_msg = await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            e2 = base_embed(title="⏰ خلص الوقت!", description=f"محدش عرفها يا رجالة! الاجابة كانت: **{item['a'][0]}**\nالجاية اسهل ان شاء الله 😅", color=THEME["red_dark"])
            return await ctx.send(embed=e2, view=WatermarkView())
        cur = get_currency(self.bot)
        if cur:
            cur.update_balance(win_msg.author.id, REWARD)
            bal = cur.get_balance(win_msg.author.id)
        else:
            bal = 0
        e3 = discord.Embed(title="🎉 عبقري الحفرة!", description=f"**{win_msg.author.mention}** جاوب صح: **{win_msg.content}**\n\n💰 خد **{REWARD:,}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{bal:,}**", color=THEME["gold"])
        e3.set_thumbnail(url=win_msg.author.display_avatar.url)
        await ctx.send(embed=e3, view=WatermarkView())


async def setup(bot):
    await bot.add_cog(Fazoora(bot))
