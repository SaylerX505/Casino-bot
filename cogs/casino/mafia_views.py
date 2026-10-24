"""Components V2 presentation and interaction views for Mafia."""

import asyncio
import logging
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
from cogs.utility.theme import THEME, StatusPanel, lobby_embed, try_edit_view

logger = logging.getLogger(__name__)


class MafiaDropdown(discord.ui.Select):
    def __init__(self, targets, view_parent):
        self.view_parent = view_parent
        options = [
            discord.SelectOption(label=player.display_name, value=str(player.id))
            for player in targets[:25]
        ]
        super().__init__(
            placeholder="اختار ضحيتك المرة دي...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view_parent.selected_id = int(self.values[0])
        self.view_parent._panel_text.content = (
            "# تم تسجيل الاختيار\nالأمر اتسجل بسرية. ارجع للشات الأساسي."
        )
        self.view_parent.disable_controls()
        await interaction.response.edit_message(view=self.view_parent)
        self.view_parent.stop()


class EphemeralActionView(LayoutView):
    def __init__(self, targets):
        super().__init__(timeout=20.0)
        self.selected_id = None
        self.dropdown = MafiaDropdown(targets, self)
        self._panel_text = TextDisplay(
            "# اختيار سري\nاختار هدفًا واحدًا من القائمة. اختيارك مش هيظهر في القناة."
        )
        self.add_item(
            Container(
                self._panel_text,
                Separator(),
                ActionRow(self.dropdown),
                accent_color=THEME["red_dark"],
            )
        )

    def disable_controls(self):
        self.dropdown.disabled = True


class SecretButtonView(LayoutView):
    def __init__(self, active_user, role_users, targets, role_name):
        super().__init__(timeout=20.0)
        self.active_user = active_user
        self.role_users = role_users
        self.targets = targets
        self.role_name = role_name
        self.selected_id = None
        self.message = None

        if role_name == "mafia":
            button_label = "افتح قائمة الاغتيال"
            image_name = "mafia_looking.jpg"
            self._panel_text = TextDisplay(
                "# حركة المافيا\nالاختيار متاح للقناص المحدد فقط. اختار ضحية بسرية."
            )
            accent = THEME["red_dark"]
        else:
            button_label = "افتح شنطة الإسعافات"
            image_name = "doctor_looking.jpg"
            self._panel_text = TextDisplay(
                "# حركة الدكتور\nالاختيار متاح للدكتور فقط. اختار لاعبًا تحميه بسرية."
            )
            accent = THEME["teal"]

        self.button = discord.ui.Button(
            label=button_label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"mafia_secret_action_{role_name}",
        )
        self.button.callback = self.btn_callback
        self.add_item(
            Container(
                self._panel_text,
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{image_name}",
                        description="لوحة الحركة السرية",
                    )
                ),
                Separator(),
                ActionRow(self.button),
                accent_color=accent,
            )
        )

    def disable_controls(self):
        self.button.disabled = True

    async def btn_callback(self, interaction: discord.Interaction):
        if interaction.user not in self.role_users:
            role_ar = "المافيا" if self.role_name == "mafia" else "الدكتور"
            return await interaction.response.send_message(
                view=StatusPanel("حركة سرية", f"الحركة دي متاحة لأفراد {role_ar} فقط.", "warning"),
                ephemeral=True,
            )

        if interaction.user != self.active_user:
            action_ar = "بالاغتيال" if self.role_name == "mafia" else "بالعلاج"
            return await interaction.response.send_message(
                view=StatusPanel(
                    "استنى دورك",
                    f"القرعة اختارت {self.active_user.mention} عشان يقوم {action_ar} الليلة دي.",
                    "warning",
                ),
                ephemeral=True,
            )

        ephemeral_view = EphemeralActionView(self.targets)
        await interaction.response.send_message(
            view=ephemeral_view,
            ephemeral=True,
        )
        await ephemeral_view.wait()

        self.selected_id = ephemeral_view.selected_id
        if self.selected_id is not None:
            self.disable_controls()
            self._panel_text.content = (
                "# الحركة اتسجلت\nتم تسجيل الاختيار السري. النتيجة هتظهر بعد نهاية الليل."
            )
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Could not disable Mafia secret-action V2 controls", exc_info=True)
            self.stop()


class VoteDropdown(discord.ui.Select):
    def __init__(self, targets, view_parent):
        self.view_parent = view_parent
        options = [
            discord.SelectOption(label=player.display_name, value=str(player.id))
            for player in targets[:25]
        ]
        super().__init__(
            placeholder="مين شاكك فيه؟ (اضغط للتصويت)",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user not in self.view_parent.alive_players:
            return await interaction.response.send_message(
                view=StatusPanel("التصويت غير متاح", "الميتين مابيتكلموش؛ التصويت للأحياء فقط.", "warning"),
                ephemeral=True,
            )

        self.view_parent.votes[interaction.user] = int(self.values[0])
        await self.view_parent.update_embed(interaction)


class DayVoteView(LayoutView):
    def __init__(self, alive_players):
        super().__init__(timeout=20.0)
        self.alive_players = alive_players
        self.votes = {}
        self.message = None
        self._panel_text = TextDisplay(self._vote_summary())
        self.dropdown = VoteDropdown(alive_players[:25], self)
        self.skip_button = discord.ui.Button(
            label="تفويت (Skip)",
            custom_id="mafia_vote_skip",
            style=discord.ButtonStyle.secondary,
        )
        self.skip_button.callback = self.skip_btn
        self.add_item(
            Container(
                self._panel_text,
                Separator(),
                ActionRow(self.dropdown),
                ActionRow(self.skip_button),
                accent_color=THEME["orange"],
            )
        )

    def _vote_summary(self):
        lines = ["# ⚖️ التصويت للإعدام", "اختار لاعبًا أو فوّت التصويت. يمكن تحديث اختيارك قبل انتهاء الوقت.", ""]
        if not self.votes:
            lines.append("في انتظار تصويت المواطنين...")
        else:
            lines.append("**الأصوات الحالية:**")
            for voter, target_id in self.votes.items():
                if target_id == "SKIP":
                    lines.append(f"{voter.mention} → ⏭️ **تفويت**")
                else:
                    target = next(
                        (player for player in self.alive_players if player.id == target_id),
                        None,
                    )
                    if target:
                        lines.append(f"{voter.mention} → 🔪 {target.mention}")
        return "\n".join(lines)[:4000]

    async def skip_btn(self, interaction: discord.Interaction):
        if interaction.user not in self.alive_players:
            return await interaction.response.send_message(
                view=StatusPanel("التصويت غير متاح", "الميتين مابيتكلموش؛ التصويت للأحياء فقط.", "warning"),
                ephemeral=True,
            )
        self.votes[interaction.user] = "SKIP"
        await self.update_embed(interaction)

    async def update_embed(self, interaction):
        self._panel_text.content = self._vote_summary()
        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.debug("Could not update Mafia V2 vote panel", exc_info=True)

        if len(self.votes) >= len(self.alive_players):
            self.stop()

    def disable_controls(self):
        self.dropdown.disabled = True
        self.skip_button.disabled = True


class MafiaLobbyView(LayoutView):
    def __init__(self, ctx, *, start_game):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.start_game = start_game
        self.players = []
        self.game_started = False
        self.time_left = 60
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام (Join)", "mafia_join", "join_button", None),
            ("مغادرة (Leave)", "mafia_leave", "leave_button", None),
            ("ابدأ الآن", "mafia_start", "start_button", None),
            ("+10 ثواني", "mafia_add_time", "add_time", "⏱️"),
        ):
            button = discord.ui.Button(label=label, custom_id=custom_id, style=discord.ButtonStyle.secondary, emoji=emoji)
            button.callback = partial(getattr(self, callback_name), button=button)
            self._buttons.append(button)
        self.add_item(Container(
            self._panel_text,
            MediaGallery(discord.MediaGalleryItem(media="attachment://menu.jpg", description="لوحة لعبة المافيا")),
            Separator(),
            ActionRow(*self._buttons),
            accent_color=THEME["red_dark"],
        ))
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        embed = lobby_embed(
            "أهلاً بيكو في الحفرة...",
            "المافيا بتتحرك في الضلمة... والدكتور بيحاول ينقذ ما يمكن إنقاذه.",
            self.players,
            self.time_left,
            max_players=25,
        )
        embed.color = discord.Color.dark_red()
        embed.set_image(url="attachment://menu.jpg")
        embed.set_footer(text="لعبة المافيا - كازينو الحفرة", icon_url=SERVER_LOGO_URL)
        return embed

    def _panel_content(self, embed=None):
        embed = embed or self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        description = f"{embed.description or ''}\n\n"
        return (title + description + f"-# لعبة المافيا • كازينو الحفرة • {config.BRAND}")[:4000]

    def _refresh_panel(self, embed=None):
        self._panel_text.content = self._panel_content(embed)

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _private_notice(self, interaction, title, description):
        await interaction.response.send_message(
            view=StatusPanel(title, description, "warning"),
            ephemeral=True,
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
                    logger.debug("Could not refresh Mafia lobby", exc_info=True)

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) < 5:
            self.game_started = True
            cancel_embed = discord.Embed(
                title="❌ تم الإلغاء",
                description="الحفرة فاضية! (تحتاج 5 لاعبين على الأقل لبدء المافيا)",
                color=discord.Color.red()
            )
            self._refresh_panel(cancel_embed)
            self._disable_buttons()
            await try_edit_view(self.message, self, context="mafia_lobby_cancelled")
            return

        self.game_started = True
        self._panel_text.content = "# المافيا بدأت\nاللوبي اتقفل. بيتم توزيع الأدوار السرية."
        self._disable_buttons()
        await try_edit_view(self.message, self, context="mafia_lobby_start")
        
        task = self.ctx.bot.loop.create_task(self.start_game(self.ctx, self.players))
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "Mafia", replace_task=self.timer_task):
            task.cancel()

    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت خلاص!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if len(self.players) >= 25:
            embed = discord.Embed(
                description="الحد الأقصى 25 لاعب!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if interaction.user in self.players:
            embed = discord.Embed(
                description="أنت سجلت اسمك ياعم!",
                color=discord.Color.red()
            )
            await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        else:
            self.players.append(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)

    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if interaction.user in self.players:
            self.players.remove(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)
        else:
            embed = discord.Embed(
                description="أنت مش في اللعبة أصلاً!",
                color=discord.Color.red()
            )
            await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")

    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        self.time_left += 10
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

class RoleRevealView(LayoutView):
    def __init__(self, alive, game_roles):
        self.alive = alive
        self.game_roles = game_roles
        super().__init__(timeout=60)
        self._panel_text = TextDisplay(
            "# تم توزيع الأدوار\nاضغط على الزرار عشان تعرف دورك في السر. احتفظ بالسر."
        )
        self.button = discord.ui.Button(
            label="اعرف دورك السري",
            emoji="👀",
            style=discord.ButtonStyle.secondary,
            custom_id="mafia_reveal_role",
        )
        self.button.callback = self.reveal_btn
        self.add_item(
            Container(
                self._panel_text,
                Separator(),
                ActionRow(self.button),
                accent_color=THEME["red_dark"],
            )
        )

    async def reveal_btn(self, interaction: discord.Interaction):
        if interaction.user not in self.alive:
            return await interaction.response.send_message(
                view=StatusPanel("مش في الجيم", "أنت مش مشارك في اللعبة دي.", "warning"),
                ephemeral=True,
            )
        role = self.game_roles[interaction.user]
        role_label = (
            "🔪 مافيا"
            if role == "mafia"
            else ("💉 دكتور" if role == "doctor" else "👤 مواطن")
        )
        await interaction.response.send_message(
            view=StatusPanel(
                "دورك السري",
                f"دورك في اللعبة هو **{role_label}**. احتفظ بالسر!",
                "success",
            ),
            ephemeral=True,
        )
