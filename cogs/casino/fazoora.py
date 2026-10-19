import discord
from discord.ext import commands
import asyncio
import random
import json
import os
from cogs.casino.game_registry import is_active_game, register_game_task, release_game_task

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
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة شغالة هنا! استنى تخلص الاول.", color=THEME["red_dark"]))

        currency = get_currency(self.bot)
        if currency is None:
            return await ctx.send(embed=base_embed(description="❌ نظام العملات مش متاح حاليًا.", color=THEME["red_dark"]))

        task = asyncio.current_task()
        if task is None or not register_game_task(self.bot, ctx.channel.id, task, ctx.author.id, "Fazoora"):
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة شغالة هنا! استنى تخلص الاول.", color=THEME["red_dark"]))

        try:
            item = random.choice(self.bank)
            question, answers = item["q"], [normalize(answer) for answer in item["a"]]
            embed = base_embed(
                title="🧠 فزورة الحفرة",
                description=f"**{question}**\n\n⏳ معاكم **30 ثانية**! اول واحد يجاوب صح يكسب **{REWARD}** {COIN_EMOJI}\nاكتب الاجابة في الشات على طول 👇",
                color=THEME["teal"],
            )
            await ctx.send(embed=embed)

            def check(message):
                return (
                    message.channel.id == ctx.channel.id
                    and not message.author.bot
                    and normalize(message.content) in answers
                )

            try:
                winner_message = await self.bot.wait_for("message", check=check, timeout=30.0)
            except asyncio.TimeoutError:
                timeout_embed = base_embed(
                    title="⏰ خلص الوقت!",
                    description=f"محدش عرفها يا رجالة! الاجابة كانت: **{item['a'][0]}**\nالجاية اسهل ان شاء الله 😅",
                    color=THEME["red_dark"],
                )
                await ctx.send(embed=timeout_embed, view=WatermarkView())
                return

            try:
                balance = currency.update_balance(winner_message.author.id, REWARD)
            except OverflowError:
                await ctx.send("✅ إجابة صحيحة، لكن رصيدك وصل للحد الأقصى المدعوم؛ المكافأة لم تُصرف.")
                return

            winner_embed = base_embed(
                title="🎉 عبقري الحفرة!",
                description=f"**{winner_message.author.mention}** جاوب صح: **{winner_message.content}**\n\n💰 خد **{REWARD:,}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{balance:,}**",
                color=THEME["gold"],
            )
            winner_embed.set_thumbnail(url=winner_message.author.display_avatar.url)
            await ctx.send(embed=winner_embed, view=WatermarkView())
        finally:
            release_game_task(self.bot, ctx.channel.id, task)


async def setup(bot):
    await bot.add_cog(Fazoora(bot))
