import asyncio
import json
import logging
import random
import re
import unicodedata

import discord
from discord.ext import commands

from cogs.casino.game_registry import (
    is_active_game,
    register_game_task,
    release_game_task,
    set_game_message,
)
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    base_embed,
    embed_to_v2,
    get_currency,
    send_status,
)

COIN_EMOJI = "<:ckrcoin:1513743032124575784>"
DATA_FILE = "data/fazoora.json"
REWARD = 30
logger = logging.getLogger(__name__)
DEFAULT_QUESTIONS = [{"q": "حاجة كل ما تاخد منها تكبر؟", "a": ["الحفرة"]}]


def normalize(s: str) -> str:
    """Normalize Arabic answers without making punctuation or diacritics matter."""
    s = unicodedata.normalize("NFKC", (s or "").casefold()).replace("ـ", "")
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        s = s.replace(source, target)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


async def credit_riddle_reward(currency, user_id: int) -> int:
    """Write the reward without blocking the Discord event loop on SQLite."""
    return await asyncio.to_thread(currency.update_balance, user_id, REWARD)


async def publish_riddle_result(ctx, panel, *, context: str) -> bool:
    """Post game outcomes as distinct V2 messages, preserving the original question."""
    try:
        await ctx.send(
            view=panel,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except discord.HTTPException:
        logger.exception(
            "Could not publish riddle result context=%s channel_id=%s",
            context,
            getattr(getattr(ctx, "channel", None), "id", None),
        )
        return False


def load_bank():
    """Load only usable questions; an empty or malformed file falls back safely."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read riddle bank; using the safe fallback question", exc_info=True)
        return list(DEFAULT_QUESTIONS)

    rows = payload.get("فزورة") if isinstance(payload, dict) else None
    valid = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            question = row.get("q")
            answers = row.get("a")
            if not isinstance(question, str) or not question.strip() or not isinstance(answers, list):
                continue
            usable_answers = [
                answer.strip()
                for answer in answers
                if isinstance(answer, str) and normalize(answer)
            ]
            if usable_answers:
                valid.append({"q": question.strip(), "a": usable_answers})
    if not valid:
        logger.warning("Riddle bank has no valid questions; using the safe fallback question")
        return list(DEFAULT_QUESTIONS)
    return valid


class Fazoora(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bank = load_bank()

    @commands.hybrid_command(name="فزورة", aliases=["فزوره", "fazoora", "احزر"], description="ابدأ فزورة جماعية")
    async def fazoora(self, ctx):
        if is_active_game(self.bot, ctx.channel.id):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة شغالة هنا بالفعل. استنى الجولة الحالية تخلص وبعدين ابدأ فزورة جديدة.",
                "warning",
            )

        if not self.bank:
            self.bank = list(DEFAULT_QUESTIONS)
        currency = get_currency(self.bot)
        if currency is None:
            return await send_status(
                ctx,
                "الاقتصاد غير متاح",
                "نظام العملات مش متاح حاليًا، فالفزورة مش هتبدأ عشان المكافآت تفضل سليمة.",
                "warning",
            )

        task = asyncio.current_task()
        if task is None or not register_game_task(self.bot, ctx.channel.id, task, ctx.author.id, "Fazoora"):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة شغالة هنا بالفعل. استنى الجولة الحالية تخلص وبعدين ابدأ فزورة جديدة.",
                "warning",
            )

        try:
            item = random.choice(self.bank)
            question, answers = item["q"], [normalize(answer) for answer in item["a"]]
            embed = base_embed(
                title="🧠 فزورة الحفرة",
                description=f"**{question}**\n\n⏳ معاكم **30 ثانية**! اول واحد يجاوب صح يكسب **{REWARD}** {COIN_EMOJI}\nاكتب الاجابة في الشات على طول 👇",
                color=THEME["teal"],
            )
            question_message = await ctx.send(
                view=embed_to_v2(embed),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            set_game_message(self.bot, ctx.channel.id, task, question_message)

            def check(message):
                return (
                    message.channel.id == ctx.channel.id
                    and not message.author.bot
                    and normalize(message.content) in answers
                )

            try:
                winner_message = await self.bot.wait_for("message", check=check, timeout=30.0)
            except TimeoutError:
                timeout_panel = StatusPanel(
                    "⏰ انتهى الوقت",
                    f"محدش وصل للإجابة المرة دي. الإجابة كانت **{item['a'][0]}**. جربوا فزورة تانية.",
                    "loss",
                )
                await publish_riddle_result(ctx, timeout_panel, context="timeout")
                return

            try:
                balance = await credit_riddle_reward(currency, winner_message.author.id)
            except OverflowError:
                panel = StatusPanel(
                    "إجابة صحيحة، لكن المكافأة لم تُصرف",
                    f"{winner_message.author.mention} جاوب صح، لكن رصيد الحساب وصل للحد الأقصى. الإجابة صحيحة، والمكافأة ما اتسجلتش.",
                    "warning",
                )
                await publish_riddle_result(ctx, panel, context="reward_cap")
                return
            except Exception:
                logger.exception("Failed to credit riddle reward user_id=%s", winner_message.author.id)
                panel = StatusPanel(
                    "تعذر صرف المكافأة",
                    f"{winner_message.author.mention} جاوب صح، لكن حصل عطل أثناء تسجيل المكافأة. تم تسجيل الخطأ للمراجعة.",
                    "warning",
                )
                await publish_riddle_result(ctx, panel, context="reward_error")
                return

            winner_embed = base_embed(
                title="🎉 عبقري الحفرة!",
                description=f"**{winner_message.author.mention}** جاوب صح: **{winner_message.content}**\n\n💰 خد **{REWARD:,}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{balance:,}**",
                color=THEME["gold"],
            )
            winner_embed.set_thumbnail(url=winner_message.author.display_avatar.url)
            winner_panel = embed_to_v2(winner_embed)
            await publish_riddle_result(ctx, winner_panel, context="winner")
        finally:
            release_game_task(self.bot, ctx.channel.id, task)


async def setup(bot):
    await bot.add_cog(Fazoora(bot))
