import random

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Select, Separator, TextDisplay

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
    def __init__(self, requester, private_root=False):
        self.requester_id = requester.id
        self.private_root = private_root
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
        view = HelpPageView(interaction.user, page)
        if self.private_root:
            await interaction.response.edit_message(view=view)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()


class HelpLayout(LayoutView):
    def __init__(self, requester, latency, private_root=False):
        super().__init__(timeout=180)
        self.private_root = private_root
        self.requester_id = requester.id
        self.message = None

        container = Container(
            TextDisplay(
                f"# Çukur • Help Center\n"
                f"**Prefix:** `{config.PREFIX}`  ·  **Latency:** {latency}ms\n\n"
                "اختار القسم المناسب لصلاحياتك. هتظهر تفاصيل الأوامر بشكل خاص ليك."
            ),
            Separator(),
            accent_color=config.EMBED_COLOR,
        )
        row = ActionRow()
        row.add_item(HelpSelect(requester, private_root=private_root))
        container.add_item(row)
        self.add_item(container)

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class HelpPageView(LayoutView):
    def __init__(self, requester, page):
        super().__init__(timeout=180)
        self.requester_id = requester.id
        self.message = None

        container = Container(
            TextDisplay(f"# {page['title']}\n{page['intro']}"),
            Separator(),
            accent_color=config.EMBED_COLOR,
        )
        fields = page["fields"]
        for index in range(0, len(fields), 3):
            entries = fields[index:index + 3]
            block = "\n\n".join(
                f"**`{config.PREFIX}{name}`**\n{description}"
                for name, description in entries
            )
            container.add_item(TextDisplay(block))

        row = ActionRow()
        back = discord.ui.Button(
            label="رجوع للأقسام",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
        )

        async def go_back(interaction: discord.Interaction):
            if interaction.user.id != self.requester_id:
                return await interaction.response.send_message(
                    "القائمة دي شخصية للي طلب المساعدة.",
                    ephemeral=True,
                )
            latency = round(interaction.client.latency * 1000)
            self.message = None
            view = HelpLayout(interaction.user, latency, private_root=True)
            await interaction.response.edit_message(view=view)
            view.message = await interaction.original_response()

        back.callback = go_back
        row.add_item(back)
        container.add_item(row)
        self.add_item(container)

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class HelpEntryView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="افتح المساعدة الخاصة", emoji="📚", style=discord.ButtonStyle.primary)
    async def open_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        latency = round(self.bot.latency * 1000)
        view = HelpLayout(interaction.user, latency, private_root=True)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()


class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", aliases=["مساعدة", "h"], description="عرض المساعدة والأوامر المتاحة")
    async def custom_help(self, ctx):
        latency = round(self.bot.latency * 1000)
        if ctx.interaction is not None:
            view = HelpLayout(ctx.author, latency, private_root=True)
            view.message = await ctx.send(view=view, ephemeral=True)
            return

        embed = discord.Embed(
            title="Çukur • Help Center",
            description=(
                f"**Prefix:** `{config.PREFIX}`  ·  **Latency:** {latency}ms\n\n"
                "افتح قائمة المساعدة الخاصة بيك من الزر تحت."
            ),
            color=config.EMBED_COLOR,
        )
        embed.set_thumbnail(url=config.SERVER_LOGO_URL)
        await ctx.send(embed=embed, view=HelpEntryView(self.bot))

    @commands.hybrid_command(name="stop", aliases=["ايقاف", "انهاء"], description="إيقاف لعبة نشطة في القناة")
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
