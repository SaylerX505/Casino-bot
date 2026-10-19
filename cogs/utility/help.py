import random

import discord
from discord.ext import commands
from discord.ui import Select, View

import config
from cogs.casino.game_registry import release_game_task
from cogs.casino.permissions import (
    has_admin_access,
    has_staff_access,
    is_bot_owner,
)


def t(text: str) -> str:
    return f".ᐟ {text} .ᐟ"


HELP_PAGES = {
    "member": {
        "title": "👤 أوامر الأعضاء",
        "intro": "الأوامر المتاحة لكل أعضاء السيرفر.",
        "fields": [
            ("مساعدة / help", "عرض قائمة المساعدة دي."),
            ("رصيد / bal", "فتح مركز الاقتصاد: اليومية، التحويل، الترتيب، والمتجر."),
            ("شنطتي / inv", "عرض الأدوات اللي في شنطتك."),
            ("بروفايل [@عضو]", "عرض الرصيد والستريك والشنطة."),
            ("استخدم صندوق أو تذكرة", "استخدام صندوق الحظ أو تذكرة الحظ من شنطتك."),
            ("حومص 100 @عضو", "تحدي حجرة ورقة مقص بمراهنة؛ لازم يكون معاك رصيد كفاية."),
            ("فزورة", "جاوب صح قبل انتهاء الوقت واكسب المكافأة."),
            ("اكشف", "استخدام أداة كشف المافيا أثناء لعبة مافيا نشطة وأنت لسه عايش."),
        ],
    },
    "staff": {
        "title": "🛡️ أوامر الستاف",
        "intro": "استضافة الألعاب دي تتطلب رتبة ستاف معتمدة أو صلاحية أدمن/مالك البوت.",
        "fields": [
            ("مافيا", "بدء لعبة مافيا."),
            ("روليت", "بدء لعبة الروليت الروسي."),
            ("غميضه", "بدء لعبة الغميضة."),
            ("جيو / geo", "بدء لعبة تخمين الموقع من الصورة."),
            ("كراسي", "بدء لعبة الكراسي الموسيقية."),
            ("عجلة / wheel", "بدء لعبة العجلة."),
            ("ايقاف / انهاء", "إيقاف اللعبة النشطة: لصاحب اللعبة أو الستاف/الأدمن/مالك البوت."),
        ],
    },
    "server_admin": {
        "title": "⚙️ إدارة السيرفر",
        "intro": "الأوامر دي تتطلب صلاحية Administrator في السيرفر أو ملكية البوت.",
        "fields": [
            ("ai / محسن", "عرض لوحة تشغيل وإيقاف محسن؛ الأزرار نفسها محمية بنفس الصلاحية."),
        ],
    },
    "owner": {
        "title": "🔒 أوامر مالك البوت",
        "intro": "الأوامر الإدارية الحساسة دي متاحة لمعرفات مالكي البوت المحددة في config فقط.",
        "fields": [
            ("rc <cog>", "إعادة تحميل إضافة واحدة. مثال: +rc geoguessr"),
            ("restart", "إعادة تشغيل البوت بعد التأكيد."),
            ("sync / rs", "مزامنة أوامر Slash."),
            ("bankreset / تصفير", "تصفير كل أرصدة وأغراض الاقتصاد؛ أمر تدميري استخدمه بحذر شديد."),
            ("bankmigrate / ترحيل", "ترحيل البيانات القديمة إلى قاعدة البنك."),
            ("whitelist add/remove @عضو أو رتبة", "إدارة قائمة السماح الخاصة بلوحة الاقتصاد."),
            ("testing", "أمر اختبار للمطور."),
        ],
    },
}


class HelpSelect(Select):
    def __init__(self, requester):
        self.requester_id = requester.id
        options = [
            discord.SelectOption(
                label="أوامر الأعضاء",
                value="member",
                emoji="👤",
                description="الاقتصاد والألعاب المتاحة للجميع",
            ),
        ]
        if has_staff_access(requester):
            options.append(discord.SelectOption(
                label="أوامر الستاف",
                value="staff",
                emoji="🛡️",
                description="استضافة الألعاب وإيقافها",
            ))
        if has_admin_access(requester):
            options.append(discord.SelectOption(
                label="إدارة السيرفر",
                value="server_admin",
                emoji="⚙️",
                description="إعدادات الإدارة",
            ))
        if is_bot_owner(requester):
            options.append(discord.SelectOption(
                label="مالك البوت",
                value="owner",
                emoji="🔒",
                description="أوامر البوت الحساسة",
            ))

        super().__init__(
            placeholder="اختار قسم الأوامر...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message(
                "القائمة دي شخصية للي طلب المساعدة. اكتب +مساعدة عشان تفتح قائمتك.",
                ephemeral=True,
            )

        category = self.values[0]
        # --- INTERACTION PERMISSIONS ---
        if category == "staff" and not has_staff_access(interaction.user):
            return await interaction.response.send_message(
                "❌ صلاحية الستاف مش متاحة لحسابك حاليًا.",
                ephemeral=True,
            )
        if category == "server_admin" and not has_admin_access(interaction.user):
            return await interaction.response.send_message(
                "❌ القسم ده مخصص لأدمن السيرفر أو مالك البوت.",
                ephemeral=True,
            )
        if category == "owner" and not is_bot_owner(interaction.user):
            return await interaction.response.send_message(
                "❌ أوامر مالك البوت مش متاحة لحسابك.",
                ephemeral=True,
            )

        page = HELP_PAGES[category]
        embed = discord.Embed(
            title=page["title"],
            description=page["intro"],
            color=config.EMBED_COLOR,
        )
        for name, description in page["fields"]:
            command = f"{config.PREFIX}{name}"
            embed.add_field(
                name=f"\u200b{command}",
                value=f"╰ {description}",
                inline=False,
            )
        embed.set_footer(text=f"Prefix: {config.PREFIX} • {config.BRAND}")
        embed.set_thumbnail(url=config.SERVER_LOGO_URL)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpView(View):
    def __init__(self, requester):
        super().__init__(timeout=180)
        self.requester_id = requester.id
        self.message = None
        self.add_item(HelpSelect(requester))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help", aliases=["مساعدة", "h"])
    async def custom_help(self, ctx):
        """Show a permission-aware help menu."""
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title=".ᐟ Help Center .ᐟ",
            description=(
                f"اختار القسم المناسب لصلاحياتك. صفحات الأوامر هتظهر لك بشكل خاص.\n"
                f"Prefix: **{config.PREFIX}**\n\n"
                f"{config.EMOJIS.get('ping', '🏓')} Latency: {latency}ms"
            ),
            color=config.EMBED_COLOR,
        )
        if getattr(config, "RANDOM_IMAGES", None):
            embed.set_image(url=random.choice(config.RANDOM_IMAGES))
        embed.set_footer(text=config.BRAND)
        embed.set_thumbnail(url=config.SERVER_LOGO_URL)

        view = HelpView(ctx.author)
        view.message = await ctx.send(embed=embed, view=view)

    @commands.command(name="stop", aliases=["ايقاف", "انهاء"])
    async def stop_game(self, ctx):
        """Stop the active game when authorized."""
        active_games = getattr(self.bot, "active_games", {})
        if ctx.channel.id not in active_games:
            return await ctx.send("❌ مفيش لعبة شغالة هنا عشان أوقفها!")

        game_info = active_games[ctx.channel.id]
        game_task = game_info.get("task")
        is_host = ctx.author.id == game_info.get("host_id")

        if not (is_host or has_staff_access(ctx.author)):
            return await ctx.send(
                "❌ إيقاف اللعبة متاح لصاحب اللعبة أو الستاف/الأدمن فقط."
            )
        if game_task is None:
            return await ctx.send("❌ مش قادر أحدد مهمة اللعبة الحالية؛ كلم الستاف.")

        game_task.cancel()
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

        release_game_task(self.bot, ctx.channel.id, game_task)
        embed = discord.Embed(
            title="🛑 إيقاف اللعبة",
            description=f"تم إنهاء لعبة **{game_info.get('name', 'غير معروفة')}** بواسطة {ctx.author.mention}.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCommand(bot))
