"""Discord Components V2 views for GeoGuessr lobby, round, and results."""

import asyncio
import logging
import time
from functools import partial

import discord
from discord.ui import (
    ActionRow,
    Container,
    LayoutView,
    MediaGallery,
    Separator,
    TextDisplay,
)

import config
from cogs.casino.game_assets import SERVER_LOGO_URL
from cogs.casino.game_registry import register_game_task
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    lobby_embed,
    private_status,
    try_edit_view,
)

logger = logging.getLogger(__name__)


# --- MENUS AND VIEWS ---
class GuessGameMenu(LayoutView):
    MAX_TIME_EXTENSIONS = 3

    def __init__(self, ctx, *, start_game):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.start_game = start_game
        self.players = set()
        self.game_started = False
        self.time_left = 40
        self.extensions_left = self.MAX_TIME_EXTENSIONS
        self.round_closed = False
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام", "geoguessr_join", "join_button", None),
            ("مغادرة", "geoguessr_leave", "leave_button", None),
            ("ابدأ الآن", "geoguessr_start", "start_button", None),
            ("+10 ثواني", "geoguessr_add_time", "add_time", "⏱️"),
        ):
            button = discord.ui.Button(label=label, custom_id=custom_id, style=discord.ButtonStyle.secondary, emoji=emoji)
            button.callback = partial(getattr(self, callback_name), button=button)
            self._buttons.append(button)
        self.add_item(Container(self._panel_text, Separator(), ActionRow(*self._buttons), accent_color=THEME["gold"]))
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        embed = lobby_embed(
            "🌍 GeoGuessr • تخمين الموقع",
            "اضغط على 'انضمام' للعب.",
            sorted(self.players, key=lambda player: player.id),
            self.time_left,
            max_players=15,
            host=self.ctx.author,
            min_players=1,
        )
        embed.color = discord.Color.gold()
        embed.set_thumbnail(url=SERVER_LOGO_URL)
        return embed

    def _sync_extension_control(self):
        exhausted = self.game_started or self.extensions_left <= 0
        for button in self._buttons:
            if button.custom_id == "geoguessr_add_time":
                button.disabled = exhausted

    def _panel_content(self):
        embed = self.get_embed()
        return (
            f"# {embed.title or 'GeoGuessr'}\n"
            f"{embed.description or ''}\n\n"
            f"**تمديدات الوقت المتبقية:** {self.extensions_left}\n\n"
            f"-# كازينو الحفرة • {config.BRAND}"
        )[:4000]

    def _refresh_panel(self, embed=None):
        embed = embed or self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        description = f"{embed.description}\n\n" if embed.description else ""
        footer = (
            f"**تمديدات الوقت المتبقية:** {self.extensions_left}\n\n"
            f"-# كازينو الحفرة • {config.BRAND}"
        )
        self._panel_text.content = (title + description + footer)[:4000]
        self._sync_extension_control()

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _send_private_embed(self, interaction, embed):
        return await private_status(
            interaction,
            embed.title or "تنبيه",
            embed.description or "حصل خطأ غير متوقع.",
            "warning",
        )

    async def run_timer(self):
        while self.time_left > 0 and not self.game_started:
            await asyncio.sleep(5)
            if self.game_started: break
            self.time_left -= 5
            if self.time_left <= 0: await self.auto_start()
            elif self.message:
                try:
                    self._refresh_panel()
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    logger.debug("Could not refresh GeoGuessr lobby", exc_info=True)

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) == 0:
            self.game_started = True
            cancel_embed = discord.Embed(
                title="❌ تم الإلغاء",
                description="انتهى وقت التسجيل من غير ما ينضم أي لاعب. افتح لوبي جديد لما تكونوا جاهزين.",
                color=discord.Color.red()
            )
            self._refresh_panel(cancel_embed)
            self._disable_buttons()
            await try_edit_view(self.message, self, context="geoguessr_lobby_cancelled")
            return

        self.game_started = True
        start_embed = discord.Embed(
            title="🚀 بدء اللعبة",
            description="جاهزين؟؟؟",
            color=discord.Color.green()
        )
        start_embed.set_thumbnail(url=SERVER_LOGO_URL)
        self._refresh_panel(start_embed)
        self._disable_buttons()
        await try_edit_view(self.message, self, context="geoguessr_lobby_start")
        
        task = self.ctx.bot.loop.create_task(self.start_game(self.ctx, list(self.players)))
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "GeoGuessr", replace_task=self.timer_task):
            task.cancel()

    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت بالفعل. لا يمكن الانضمام بعد إغلاق التسجيل.",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if len(self.players) >= 15:
            embed = discord.Embed(
                description="الحد الأقصى 15 لاعب!",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if interaction.user in self.players:
            embed = discord.Embed(
                description="أنت مسجل بالفعل في اللوبي.",
                color=discord.Color.red()
            )
            await self._send_private_embed(interaction, embed)
        else:
            self.players.add(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)

    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت بالفعل. لا يمكن مغادرة الجولة بعد بدايتها.",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if interaction.user in self.players:
            self.players.discard(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)
        else:
            embed = discord.Embed(
                description="أنت مش مسجل في اللوبي، فلازم تنضم الأول قبل المغادرة.",
                color=discord.Color.red()
            )
            await self._send_private_embed(interaction, embed)

    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="صاحب اللوبي فقط يقدر يبدأ الجولة.",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="صاحب اللوبي فقط يقدر يمدد وقت التسجيل.",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if self.game_started:
            return await private_status(
                interaction,
                "اللوبي اتقفل",
                "اللعبة بدأت بالفعل؛ مفيش تمديدات بعد إغلاق التسجيل.",
                "warning",
            )
        if self.extensions_left <= 0:
            return await private_status(
                interaction,
                "وصلنا للحد الأقصى",
                "استخدمت كل تمديدات الوقت المتاحة. ابدأ الجولة أو استنى العدّ التنازلي.",
                "warning",
            )
        self.extensions_left -= 1
        self.time_left += 10
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

class GeoGuessrResultPanel(LayoutView):
    def __init__(self, title, description, outcome="neutral", image_name=None):
        super().__init__(timeout=120)
        accents = {
            "success": THEME["green"],
            "win": THEME["gold"],
            "warning": THEME["orange"],
            "neutral": THEME["grey"],
        }
        children = [TextDisplay(f"# {title}\n{description}"[:4000])]
        if image_name:
            children.extend([
                Separator(),
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{image_name}",
                        description="صورة من نتائج جولة GeoGuessr",
                    )
                ),
            ])
        self.add_item(Container(*children, accent_color=accents.get(outcome, THEME["grey"])))


class ActiveGameView(LayoutView):
    MAX_TIME_EXTENSIONS = 2

    def __init__(self, executor, timer_state, round_num=1, total_rounds=6):
        super().__init__(timeout=120)
        self.executor = executor
        self.timer_state = timer_state
        self.round_num = round_num
        self.total_rounds = total_rounds
        self.extensions_left = self.MAX_TIME_EXTENSIONS
        self.message = None
        self._panel_text = TextDisplay(self._content())
        self.button = discord.ui.Button(
            label="+10 ثواني للحل",
            style=discord.ButtonStyle.secondary,
            emoji="⏱️",
            custom_id=f"geoguessr_add_time_{round_num}",
        )
        self.button.callback = self.add_time
        self.add_item(
            Container(
                self._panel_text,
                MediaGallery(
                    discord.MediaGalleryItem(
                        media="attachment://streetview.png",
                        description="صورة الجولة",
                    )
                ),
                Separator(),
                ActionRow(self.button),
                accent_color=THEME["teal"],
            )
        )

    def _content(self, remaining=None):
        if remaining is None:
            remaining = max(0, int(self.timer_state["end_time"] - time.time()))
        return (
            f"# 🌍 الجولة {self.round_num} من {self.total_rounds} — أين نحن؟\n"
            "اكتب اسم الدولة بالعربية أو الإنجليزية في الشات.\n"
            f"الوقت المتبقي: **{remaining}** ثانية.\n"
            f"**تمديدات الوقت المتبقية:** {self.extensions_left}\n\n"
            f"-# كازينو الحفرة • {config.BRAND}"
        )

    def update_timer(self, remaining):
        self._panel_text.content = self._content(remaining)
        if remaining <= 0:
            self.disable_controls()

    def disable_controls(self):
        self.round_closed = True
        self.button.disabled = True

    async def add_time(self, interaction: discord.Interaction):
        if interaction.user != self.executor:
            return await interaction.response.send_message(
                view=StatusPanel("التحكم مش متاح", "مضيف اللعبة فقط يقدر يمدد الوقت.", "warning"),
                ephemeral=True,
            )
        if self.round_closed or time.time() >= self.timer_state["end_time"]:
            self.disable_controls()
            return await interaction.response.send_message(
                view=StatusPanel("الجولة انتهت", "انتهى وقت التخمين بالفعل؛ مش ممكن نفتح الجولة من جديد.", "warning"),
                ephemeral=True,
            )
        if self.extensions_left <= 0:
            self.button.disabled = True
            return await interaction.response.send_message(
                view=StatusPanel(
                    "وصلنا للحد الأقصى",
                    "استخدمت كل تمديدات الجولة. استمر في التخمين؛ مفيش تمديدات إضافية.",
                    "warning",
                ),
                ephemeral=True,
            )

        self.extensions_left -= 1
        self.timer_state["end_time"] += 10
        remaining = max(0, int(self.timer_state["end_time"] - time.time()))
        self.update_timer(remaining)
        await interaction.response.send_message(
            view=StatusPanel(
                "تم تمديد الوقت",
                f"أضفنا 10 ثواني للجولة الحالية. التمديدات المتبقية: **{self.extensions_left}**.",
                "success",
            ),
            ephemeral=True,
        )
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Could not refresh GeoGuessr V2 panel after time extension", exc_info=True)
