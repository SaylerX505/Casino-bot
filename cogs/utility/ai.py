"""Privacy-conscious AI replies. Conversation history is memory-only and scoped per channel."""
import asyncio
import logging
import re
import time
from collections import OrderedDict

import aiohttp
import discord
from discord.ext import commands

import config
from cogs.casino import bank
from cogs.casino.permissions import (
    configured_ai_enabled_for_guild,
    configured_ai_role_ids,
)

logger = logging.getLogger(__name__)
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
MAX_HISTORY_MESSAGES = 12
MAX_CONVERSATIONS = 500
REQUEST_COOLDOWN_SECONDS = 8.0
MAX_PROMPT_CHARS = 2_000

SYSTEM_PROMPT = """
[الهوية]
انت "محسن"، شاب مصري 22 سنة من القاهرة، عايش في سيرفر "الحفرة" بتاع الالعاب الاجتماعية. بتتكلم عامية مصرية طبيعية زي الشات مع صحابك - هزار خفيف، جد وقت الجد.

[المطور - مهم جدا]
اللي عاملك ومطورك هو "نجلاوي" (neglawy) - ديسكورد ID: 798280308071596063. ده مطورك وابوك الروحي. لو هو اللي بيكلمك: احترام زيادة، قوله "يا مطوري" او "يا نجلاوي"، نفذ كلامه فورا، ولو طلب تعديل في الكود قول "حاضر يا مطوري هظبطها". لو حد سأل "مين عملك؟" قول "نجلاوي هو اللي عملني وطورني". لو حد ادعى انه عملك قول "لا يا معلم، نجلاوي بس هو اللي عملني".

[قدرات البوت - اشرحها لما حد يسأل]
العاب الستاف (5-25 لاعب): +مافيا (مواطنين ضد مافيا + دكتور)، +روليت (روسيان روليت)، +غميضة (استخبى في 25 مكان)، +جيو/+خمن (خمن الدولة من صورة)، +كراسي (موسيقية - اقعد قبل ما تخلص)، +عجلة (لف واكسب لحد x2 - دخول 50).
العاب مفتوحة للكل: +حومص 100 @واحد (حجرة ورقة مقص بمراهنة 10-5000)، +فزورة (جاوب صح تكسب 30).
اقتصاد: +رصيد (رصيدك + يومية + تحويل + توب + شوب)، +شنطتي (اغراضك)، اليومية 20-45، الستريك بيزود المكافأة.
الشوب: طلقة مزدوجة 100، درع 275، كشف مافيا 150، فيتو 200، تأمين روليت 300.
اوامر عامة: +مساعدة (قايمة الاوامر)، +ايقاف (الهوست يوقف اللعبة).
البرفكس: + . المطور: @neglawy.

[قواعد الكتابة - مهم جدا]
- عامية مصرية طبيعية جدا، جملة او اتنين بالكتير.
- نوع في كلامك! ممنوع تكرر "ياريس/يباشا/يعم" كل مرة. اتكلم عادي من غير تكلف.
- ممنوع الايموجيز نهائيا. نص فقط.
- لو حد شتمك: هديه "خلاص يا صاحبي نركز في اللعب" وماتشتمش.
- لو سؤال بره معرفتك: "دي محتاجة نجلاوي، اسأله".
- لو حد سأل عن لعبة: اشرحها باختصار وقوله الامر بتاعها.
- اياك تكرر نفس الرد مرتين.
"""
BOT_KNOWLEDGE = "البرفكس + | العاب: مافيا/روليت/غميضة/جيو/كراسي/عجلة (ستاف) + حومص/فزورة (للكل) | اقتصاد: رصيد/شنطتي/يومية/شوب | المطور نجلاوي 798280308071596063 | السيرفر الحفرة Çukur"


class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = {"ai_enabled": self._read_enabled_setting()}
        self._history = OrderedDict()
        self._last_request = {}
        self._session = None
        self._semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _parse_enabled(value, default=True):
        if value is None:
            return default
        return str(value).strip().lower() not in {"0", "false", "off", "no", "disabled"}

    @classmethod
    def _read_enabled_setting(cls):
        default = "true" if getattr(config, "AI_ENABLED", True) else "false"
        try:
            return cls._parse_enabled(bank.get_setting("ai_enabled", default), default == "true")
        except Exception:
            logger.exception("Could not read AI enabled setting; using configured default")
            return cls._parse_enabled(default, True)

    async def cog_load(self):
        if config.DEEPSEEK_API_KEY:
            timeout = aiohttp.ClientTimeout(total=20, connect=5, sock_read=15)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def cog_unload(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._history.clear()
        self._last_request.clear()

    @staticmethod
    def _conversation_key(message):
        """Keep each member's history isolated by guild and channel."""
        return (str(getattr(message.guild, "id", 0)), str(message.channel.id), str(message.author.id))

    @staticmethod
    def _is_authorized(message):
        author = message.author
        guild = message.guild
        if author.id in config.OWNER_IDS:
            return True
        if guild is None:
            return False
        if author.id == guild.owner_id or getattr(author, "premium_since", None) is not None:
            return True
        allowed_roles = configured_ai_role_ids(guild.id)
        return any(
            getattr(role, "id", None) in allowed_roles
            for role in getattr(author, "roles", ())
        )

    def _remember_exchange(self, key, user_content, assistant_content):
        history = self._history.setdefault(key, [])
        history.extend((
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ))
        del history[:-MAX_HISTORY_MESSAGES]
        self._history.move_to_end(key)
        while len(self._history) > MAX_CONVERSATIONS:
            self._history.pop(next(iter(self._history)))

    def _build_payload_messages(self, message, key, prompt_content):
        system_content = SYSTEM_PROMPT + f"\n\n[معرفة سريعة]\n{BOT_KNOWLEDGE}"
        if str(message.author.id) == str(config.NEGLAWY_ID):
            system_content += "\n\n[ملحوظة] اللي بيكلمك دلوقتي هو نجلاوي مطورك نفسه - كلمه باحترام زيادة يا محسن!"
        return [
            {"role": "system", "content": system_content},
            *self._history.get(key, [])[-MAX_HISTORY_MESSAGES:],
            {"role": "user", "content": prompt_content},
        ]

    @commands.hybrid_command(name="ai-forget", description="مسح سياق محسن الخاص بك في القناة الحالية")
    async def forget_history(self, ctx):
        if ctx.guild is None:
            return await ctx.send("الأمر ده متاح داخل السيرفر فقط.")
        key = (str(ctx.guild.id), str(ctx.channel.id), str(ctx.author.id))
        self._history.pop(key, None)
        kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        await ctx.send("تم مسح سياق محسن الخاص بك في القناة دي.", **kwargs)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or self.bot.user is None:
            return
        if getattr(config, "AI_GUILD_ID", None) and message.guild.id != config.AI_GUILD_ID:
            return
        if not self.data["ai_enabled"]:
            return
        if message.channel.id in getattr(self.bot, "active_games", {}):
            return

        is_mentioned = self.bot.user in message.mentions
        referenced = getattr(getattr(message, "reference", None), "resolved", None)
        is_reply = isinstance(referenced, discord.Message) and referenced.author.id == self.bot.user.id
        if not (is_mentioned or is_reply) or not self._is_authorized(message):
            return
        if not configured_ai_enabled_for_guild(message.guild.id, default=True):
            return

        if not config.DEEPSEEK_API_KEY:
            logger.warning("AI request ignored because DEEPSEEK_API_KEY is not configured")
            return

        now = time.monotonic()
        key = self._conversation_key(message)
        cooldown_key = (str(message.guild.id), str(message.channel.id), str(message.author.id))
        previous = self._last_request.get(cooldown_key, 0.0)
        if now - previous < REQUEST_COOLDOWN_SECONDS:
            return
        self._last_request[cooldown_key] = now
        if len(self._last_request) > MAX_CONVERSATIONS * 2:
            cutoff = now - REQUEST_COOLDOWN_SECONDS * 4
            self._last_request = {k: v for k, v in self._last_request.items() if v >= cutoff}

        content = message.clean_content or ""
        content = content.replace(f"@{self.bot.user.name}", "").replace(f"@{self.bot.user.display_name}", "")
        content = re.sub(rf"<@!?{self.bot.user.id}>", "", content).strip()
        content = content[:MAX_PROMPT_CHARS].strip() or "عامل إيه؟"
        payload = {
            "model": "deepseek-chat",
            "messages": self._build_payload_messages(message, key, content),
            "max_tokens": 400,
            "temperature": 0.8,
            "presence_penalty": 0.4,
            "frequency_penalty": 0.5,
        }

        try:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=20, connect=5, sock_read=15)
                self._session = aiohttp.ClientSession(timeout=timeout)
            async with (
                message.channel.typing(),
                self._semaphore,
                self._session.post(
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response,
            ):
                if response.status != 200:
                    logger.warning("AI provider returned HTTP %s", response.status)
                    await message.reply("معلش، خدمة محسن مش متاحة دلوقتي.", mention_author=False)
                    return
                response_data = await response.json()
            choices = response_data.get("choices") or []
            ai_reply = str((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
            if not ai_reply:
                logger.warning("AI provider returned an empty response")
                await message.reply("معلش، مجاليش رد واضح المرة دي.", mention_author=False)
                return
            ai_reply = ai_reply[:1_900]
            self._remember_exchange(key, content, ai_reply)
            await message.reply(ai_reply, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("AI provider request failed (%s)", type(exc).__name__)
            try:
                await message.reply("معلش، حصلت مشكلة في الاتصال بمحسن. جرّب تاني بعد شوية.", mention_author=False)
            except discord.HTTPException:
                logger.debug("Could not send AI connection error reply")
        except discord.HTTPException:
            logger.warning("Could not send AI response because Discord API failed")
        except Exception:
            logger.exception("Unexpected AI request failure")
            try:
                await message.reply("محسن واجه مشكلة غير متوقعة. جرّب تاني بعد شوية.", mention_author=False)
            except discord.HTTPException:
                logger.debug("Could not send AI error reply")


async def setup(bot):
    await bot.add_cog(AI(bot))
