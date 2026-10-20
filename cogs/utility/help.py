import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Select, Separator, TextDisplay

import config
from cogs.casino.game_registry import release_game_task
from cogs.utility.theme import StatusPanel
from cogs.casino.permissions import (
    has_admin_access,
    has_staff_access,
)


def t(text: str) -> str:
    return f".ᐟ {text} .ᐟ"


SLASH_COMMANDS = {
    "مساعدة": "help",
    "رصيد": "bal",
    "شنطتي": "inv",
    "بروفايل [@عضو]": "بروفايل",
    "استخدم صندوق أو تذكرة": "استخدم",
    "حومص 100 @عضو": "حومص",
    "فزورة": "فزورة",
    "اكشف": "اكشف",
    "مافيا": "مافيا",
    "روليت": "روليت",
    "غميضه": "غميضه",
    "جيو": "جيو",
    "كراسي": "كراسي",
    "عجلة": "عجلة",
    "ايقاف": "stop",
}


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
        "intro": "استضافة الألعاب دي تتطلب رتبة ستاف معتمدة أو صلاحية Administrator.",
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
            lines = []
            for name, description in entries:
                primary = name.split(" / ")[0].strip()
                lines.append(
                    f"**`{config.PREFIX}{primary}`**"
                    + (f" · **`/{SLASH_COMMANDS[primary]}`**" if primary in SLASH_COMMANDS else "")
                    + f"\n{description}"
                )
            container.add_item(TextDisplay("\n\n".join(lines)))

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

    @discord.ui.button(label="افتح المساعدة الخاصة", emoji="📚", style=discord.ButtonStyle.secondary)
    async def open_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        latency = round(self.bot.latency * 1000)
        view = HelpLayout(interaction.user, latency, private_root=True)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()


class HelpCommand(commands.Cog):
    games = app_commands.Group(
        name="games",
        description="اختار لعبة من ألعاب Çukur.",
    )
    economy = app_commands.Group(
        name="economy",
        description="إدارة الرصيد والشنطة والمتجر.",
    )

    async def _invoke_hybrid_command(self, interaction: discord.Interaction, command_name: str):
        command = self.bot.get_command(command_name)
        if command is None:
            return await interaction.response.send_message(
                "الأمر المطلوب مش متاح حاليًا. افتح قائمة المساعدة أو بلّغ الستاف.",
                ephemeral=True,
            )

        ctx = None
        try:
            ctx = await commands.Context.from_interaction(interaction)
            await command.invoke(ctx)
        except commands.CommandError as error:
            if ctx is not None:
                await command.dispatch_error(ctx, error)
            elif not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ مقدرناش نشغّل الأمر ده. جرّب من قائمة المساعدة.",
                    ephemeral=True,
                )
        except Exception as error:
            if ctx is not None:
                await command.dispatch_error(ctx, commands.CommandInvokeError(error))
            elif not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ حصلت مشكلة أثناء تشغيل الأمر. اتبلغ الستاف.",
                    ephemeral=True,
                )

    @games.command(name="mafia", description="بدء لعبة المافيا بصلاحية الستاف.")
    async def games_mafia(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "مافيا")

    @games.command(name="roulette", description="بدء الروليت الروسي بصلاحية الستاف.")
    async def games_roulette(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "روليت")

    @games.command(name="hide-and-seek", description="بدء لعبة الغميضة بصلاحية الستاف.")
    async def games_hide_and_seek(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "غميضه")

    @games.command(name="geo", description="بدء لعبة تخمين الموقع بصلاحية الستاف.")
    async def games_geo(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "جيو")

    @games.command(name="chairs", description="بدء لعبة الكراسي بصلاحية الستاف.")
    async def games_chairs(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "كراسي")

    @games.command(name="wheel", description="بدء عجلة الحظ بصلاحية الستاف.")
    async def games_wheel(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "عجلة")

    @games.command(name="riddle", description="بدء فزورة متاحة للأعضاء.")
    async def games_riddle(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "فزورة")

    @economy.command(name="balance", description="فتح مركز الاقتصاد والرصيد الخاص بك.")
    async def economy_balance(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "bal")

    @economy.command(name="inventory", description="عرض شنطتك وأغراضك القابلة للاستخدام.")
    async def economy_inventory(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "inv")

    @economy.command(name="profile", description="عرض بطاقة بروفايلك في الحفرة.")
    async def economy_profile(self, interaction: discord.Interaction):
        await self._invoke_hybrid_command(interaction, "بروفايل")

    @economy.command(name="daily", description="استلام المكافأة اليومية والستريك.")
    async def economy_daily(self, interaction: discord.Interaction):
        from cogs.casino.currency import CurrencyHubLayout

        cog = self.bot.get_cog("Currency")
        if cog is None:
            return await interaction.response.send_message(
                "مركز الاقتصاد مش جاهز حاليًا. جرّب بعد شوية.",
                ephemeral=True,
            )
        await CurrencyHubLayout(cog, interaction.user).daily_btn(interaction)

    @economy.command(name="top", description="عرض ترتيب أغنى الأعضاء بشكل خاص.")
    async def economy_top(self, interaction: discord.Interaction):
        from cogs.casino.currency import CurrencyHubLayout

        cog = self.bot.get_cog("Currency")
        if cog is None:
            return await interaction.response.send_message(
                "مركز الاقتصاد مش جاهز حاليًا. جرّب بعد شوية.",
                ephemeral=True,
            )
        await CurrencyHubLayout(cog, interaction.user).top_btn(interaction)

    @economy.command(name="shop", description="فتح المتجر واختيار غرض للشراء.")
    async def economy_shop(self, interaction: discord.Interaction):
        from cogs.casino.currency import ShopView

        cog = self.bot.get_cog("Currency")
        if cog is None:
            return await interaction.response.send_message(
                "المتجر مش جاهز حاليًا. جرّب بعد شوية.",
                ephemeral=True,
            )
        await interaction.response.send_message(view=ShopView(cog), ephemeral=True)

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
        active_games = getattr(self.bot, "active_games", {})
        game_info = active_games.get(ctx.channel.id)
        if game_info is None:
            return await ctx.send(
                view=StatusPanel("مفيش لعبة شغالة", "مفيش لعبة نشطة في القناة دي.", "warning"),
                ephemeral=ctx.interaction is not None,
            )

        is_host = ctx.author.id == game_info.get("host_id")
        if not (is_host or has_staff_access(ctx.author)):
            return await ctx.send(
                view=StatusPanel("صلاحية غير كافية", "إيقاف اللعبة متاح لصاحبها أو للستاف المصرح له.", "warning"),
                ephemeral=ctx.interaction is not None,
            )

        game_task = game_info.get("task")
        if game_task is None:
            return await ctx.send(
                view=StatusPanel("تعذر إيقاف اللعبة", "مهمة اللعبة مش متاحة؛ بلّغ الستاف.", "warning"),
                ephemeral=ctx.interaction is not None,
            )

        view = game_info.get("view")
        message = getattr(view, "message", None) if view is not None else None
        message = message or game_info.get("message")
        if ctx.interaction is not None:
            await ctx.defer(ephemeral=True)

        game_task.cancel()
        if view is not None:
            view.stop()
        try:
            await asyncio.wait_for(asyncio.shield(game_task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            self.bot.dispatch("game_shutdown_error", ctx.channel.id, game_task)
        finally:
            release_game_task(self.bot, ctx.channel.id, game_task)

        stopped_view = StatusPanel(
            "🛑 تم إيقاف اللعبة",
            f"تم إنهاء **{game_info.get('name', 'اللعبة')}** بواسطة {ctx.author.mention}. تقدروا تبدأوا لعبة جديدة.",
            "warning",
        )
        if message is not None:
            try:
                await message.edit(content=None, embeds=[], attachments=[], view=stopped_view)
            except discord.HTTPException:
                pass

        if ctx.interaction is not None:
            await ctx.interaction.edit_original_response(
                view=StatusPanel("تم الإيقاف", "اتقفلت اللعبة واتنضفت حالتها.", "success"),
            )
        elif message is None:
            await ctx.send(view=stopped_view)

async def setup(bot):
    await bot.add_cog(HelpCommand(bot))
