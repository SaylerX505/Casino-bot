# cogs/utility/ai.py
import discord
from discord.ext import commands
import aiohttp
import config
from cogs.casino.permissions import has_admin_access, is_admin_or_owner
import json
import os

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
AI_DATA_FILE = "data/ai_data.json"
WHITELISTED_ROLE_IDS = [1528494324805927114, 1518803246049919198, 1512243537435955331, 1492986771879760137, 1486759788078432517]

NEGLAWY_ID = 798280308071596063
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

def load_ai_data():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(AI_DATA_FILE):
        default_data = {"ai_enabled": True, "user_history": {}}
        save_ai_data(default_data)
        return default_data
    try:
        with open(AI_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "ai_enabled" not in data: data["ai_enabled"] = True
            if "user_history" not in data: data["user_history"] = {}
            return data
    except Exception as e:
        print(f"Error loading AI data: {e}")
        return {"ai_enabled": True, "user_history": {}}

def save_ai_data(data):
    os.makedirs("data", exist_ok=True)
    with open(AI_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


class AIToggleView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Mohsen (ON)", style=discord.ButtonStyle.success, emoji="🟢")
    async def turn_on(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_access(interaction.user):
            return await interaction.response.send_message("للأدمنز والأونرز بس!", ephemeral=True)
            
        self.cog.data["ai_enabled"] = True
        save_ai_data(self.cog.data)
        embed = discord.Embed(
            description="✅ Mohsen is on",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Mohsen (Off)", style=discord.ButtonStyle.danger, emoji="🔴")
    async def turn_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_access(interaction.user):
            return await interaction.response.send_message("للأدمنز والأونرز بس!", ephemeral=True)
            
        self.cog.data["ai_enabled"] = False
        save_ai_data(self.cog.data)
        embed = discord.Embed(
            description="Mohsen is off ❌",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=self)


class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_ai_data()

    @commands.command(name="ai", aliases=["محسن", "ذكاء"])
    @is_admin_or_owner()
    async def toggle_ai(self, ctx):
        """تحكم في تشغيل أو إيقاف الذكاء الاصطناعي (محسن)"""
        status = "شغال 🟢" if self.data["ai_enabled"] else "متوقف 🔴"
        embed = discord.Embed(
            title="🤖 إعدادات الذكاء الاصطناعي (محسن)",
            description=f"الحالة الحالية: **{status}**\n\nإضغط على الأزرار بالأسفل للتفعيل أو الإيقاف:",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Developed by @neglawy • {config.BRAND}")
        await ctx.send(embed=embed, view=AIToggleView(self))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id != self.bot.user.id:
            return

        if not self.data["ai_enabled"]:
            return

        if hasattr(self.bot, 'active_games') and message.channel.id in self.bot.active_games:
            return

        is_mentioned = self.bot.user in message.mentions
        is_reply = False
        if message.reference and message.reference.resolved:
            if isinstance(message.reference.resolved, discord.Message):
                if message.reference.resolved.author == self.bot.user:
                    is_reply = True

        if not (is_mentioned or is_reply):
            return

        # === AI ACCESS WHITELIST ===
        is_authorized = False
        if message.author.id in config.OWNER_IDS:
            is_authorized = True
        elif message.guild:
            if message.author.id == message.guild.owner_id:
                is_authorized = True
            elif message.author.premium_since is not None:
                is_authorized = True
            elif hasattr(message.author, 'roles') and any(r.id in WHITELISTED_ROLE_IDS for r in message.author.roles):
                is_authorized = True
                
        if not is_authorized:
            return # Silently ignore unauthorized users
        # ===========================

        prompt_content = message.clean_content.replace(f"@{self.bot.user.name}", "").strip()
        if not prompt_content:
            prompt_content = "عامل ايه"

        user_id = str(message.author.id)
        if user_id not in self.data["user_history"]:
            self.data["user_history"][user_id] = []

        # Save user message to JSON history
        self.data["user_history"][user_id].append({
            "role": "user",
            "content": f"{message.author.display_name}: {prompt_content}"
        })
        
        # Keep only the last 10 messages per user to save disk space and context window
        if len(self.data["user_history"][user_id]) > 10:
            self.data["user_history"][user_id] = self.data["user_history"][user_id][-10:]
            
        save_ai_data(self.data)

        # Get channel history for context
        channel_history = []
        async for msg in message.channel.history(limit=10):
            if msg.author.bot and msg.author.id != self.bot.user.id:
                continue
            channel_history.append(f"{msg.author.display_name}: {msg.clean_content[:100]}")
        
        channel_history.reverse()
        context_text = "\n".join(channel_history[-5:])

        async with message.channel.typing():
            try:
                headers = {
                    "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                }
                
                messages_payload = [
                    {"role": "system", "content": SYSTEM_PROMPT + f"\n\n[معرفة سريعة]\n{BOT_KNOWLEDGE}\n\n[سياق الشات حالياً]\n{context_text}" + (f"\n\n[ملحوظة] اللي بيكلمك دلوقتي هو نجلاوي مطورك نفسه (ID 798280308071596063) - كلمه باحترام زيادة يا محسن!" if str(message.author.id) == str(NEGLAWY_ID) else "")}
                ]
                
                for msg_data in self.data["user_history"][user_id][-7:]:
                    messages_payload.append({"role": msg_data["role"], "content": msg_data["content"]})

                payload = {
                    "model": "deepseek-chat",
                    "messages": messages_payload,
                    "max_tokens": 200,
                    "temperature": 0.9,
                    "presence_penalty": 0.7, 
                    "frequency_penalty": 0.7 
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=15) as resp:
                        if resp.status == 200:
                            api_data = await resp.json()
                            ai_reply = api_data['choices'][0]['message']['content'].strip()
                            
                            # Save bot reply to JSON history
                            self.data["user_history"][user_id].append({
                                "role": "assistant",
                                "content": ai_reply
                            })
                            save_ai_data(self.data)
                            
                            await message.reply(ai_reply, mention_author=True)
                        else:
                            await message.reply("معلش، المخ مهنج شوية دلوقتي.", mention_author=True)
            except Exception as e:
                print(f"AI Error: {e}")

async def setup(bot):
    await bot.add_cog(AI(bot))