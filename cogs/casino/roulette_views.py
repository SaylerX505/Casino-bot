"""Reusable Components V2 views for roulette player selection and item actions."""

import asyncio
import logging
import time

import discord
from discord.ui import (
    ActionRow,
    Container,
    LayoutView,
    MediaGallery,
    Separator,
    TextDisplay,
)

from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    private_status,
)

logger = logging.getLogger(__name__)


class KickSelect(discord.ui.Select):
    def __init__(self, chosen_player, chunk_targets, active_players, part_num, total_parts):
        self.chosen_player = chosen_player
        options = []
        for player in chunk_targets:
            real_index = active_players.index(player) + 1
            options.append(
                discord.SelectOption(
                    label=f"{real_index}- {player.display_name}",
                    value=str(player.id),
                    description="يطير بره اللعبة",
                )
            )
        placeholder = "اختار ضحية..." if total_parts == 1 else f"اختار ضحية... (قائمة {part_num})"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.view.is_finished() or time.monotonic() >= self.view.deadline:
            self.view.disable_controls()
            self.view._panel_text.content = (
                f"# انتهى وقت الجولة {self.view.round_num}\n"
                "مهلة اختيار الأهداف انتهت. مفيش اختيار متأخر هيتسجل."
            )
            return await private_status(
                interaction,
                "انتهى وقت الاختيار",
                "قفلنا اختيار الجولة؛ مش ممكن تسجيل هدف بعد انتهاء المهلة.",
                "warning",
            )

        if interaction.user != self.chosen_player:
            return await interaction.response.send_message(
                view=StatusPanel("مش دورك", "اختيار الضحية متاح للقناص المختار فقط.", "warning"),
                ephemeral=True,
            )

        selected_id = int(self.values[0])
        if selected_id in self.view.kicked_ids:
            return await interaction.response.send_message(
                view=StatusPanel("اختيار مكرر", "أنت اخترت اللاعب ده بالفعل. اختار شخصًا آخر.", "warning"),
                ephemeral=True,
            )

        self.view.kicked_ids.append(selected_id)
        if self.view.double_shot_active and len(self.view.kicked_ids) < 2:
            return await interaction.response.send_message(
                view=StatusPanel("تم تسجيل الضحية الأولى", "اختار ضحية ثانية بسرعة.", "success"),
                ephemeral=True,
            )

        self.view._panel_text.content = (
            "# تم تسجيل الاختيار\nالاختيار اتسجل. بنجهز نتيجة الجولة دلوقتي."
        )
        await interaction.response.send_message(
            view=StatusPanel("تم اعتماد الاختيار", "استنى نتيجة الجولة.", "success"),
            ephemeral=True,
        )
        self.view.stop()


class KickView(DiagnosticViewMixin, LayoutView):
    def __init__(self, chosen_player, active_players, has_weapon, currency_cog, round_num=1):
        super().__init__(timeout=20.0)
        self.deadline = time.monotonic() + 20.0
        self.kicked_ids = []
        self.chosen_player = chosen_player
        self.double_shot_active = False
        self.currency_cog = currency_cog
        self.message = None
        self.round_num = round_num
        self._panel_text = TextDisplay(
            f"# 🎯 الروليت — الجولة {round_num}\n"
            f"القناص المختار: {chosen_player.mention}\n"
            "قدامك 20 ثانية تختار ضحية من القائمة. الاختيار النهائي بيظهر بعد اعتمادك."
        )
        components = [
            self._panel_text,
            MediaGallery(
                discord.MediaGalleryItem(
                    media="attachment://sniper.png",
                    description="صورة القناص المختار",
                )
            ),
            Separator(),
        ]
        self._action_rows = []
        self.weapon_btn = None
        if has_weapon:
            self.weapon_btn = discord.ui.Button(
                label="سلاح الحفرة",
                style=discord.ButtonStyle.secondary,
                emoji="🔫",
                custom_id="roulette_use_weapon",
            )
            self.weapon_btn.callback = self.weapon_callback
            self._action_rows.append(ActionRow(self.weapon_btn))

        targets = [player for player in active_players if player != chosen_player]
        chunk_size = 25
        total_parts = (len(targets) - 1) // chunk_size + 1
        self._weapon_lock = asyncio.Lock()
        self._selects = []
        for index in range(0, len(targets), chunk_size):
            select = KickSelect(
                chosen_player,
                targets[index:index + chunk_size],
                active_players,
                (index // chunk_size) + 1,
                total_parts,
            )
            self._selects.append(select)
            self._action_rows.append(ActionRow(select))

        self.add_item(Container(*components, *self._action_rows, accent_color=THEME["red_dark"]))

    def disable_controls(self):
        if self.weapon_btn is not None:
            self.weapon_btn.disabled = True
        for select in self._selects:
            select.disabled = True

    async def weapon_callback(self, interaction: discord.Interaction):
        if self.is_finished() or time.monotonic() >= self.deadline:
            self.disable_controls()
            self._panel_text.content = (
                f"# انتهى وقت الجولة {self.round_num}\n"
                "مهلة استخدام السلاح انتهت. مفيش أداة إضافية اتخصمت."
            )
            return await private_status(
                interaction,
                "انتهى وقت الجولة",
                "اتقفلت الجولة؛ مش ممكن استخدام السلاح بعد انتهاء المهلة.",
                "warning",
            )

        if interaction.user != self.chosen_player:
            return await private_status(
                interaction,
                "السلاح مش بتاعك",
                "استخدام السلاح متاح للقناص المختار فقط.",
                "warning",
            )

        async with self._weapon_lock:
            if self.is_finished() or time.monotonic() >= self.deadline:
                self.disable_controls()
                return await private_status(
                    interaction,
                    "انتهى وقت الجولة",
                    "اتقفلت الجولة قبل تأكيد استخدام السلاح؛ راجع شنطتك لو عندك شك في حالة الأداة.",
                    "warning",
                )
            if self.double_shot_active or self.weapon_btn is None or self.weapon_btn.disabled:
                return await private_status(
                    interaction,
                    "السلاح اتفعل بالفعل",
                    "سلاح الحفرة بيتستخدم مرة واحدة في الجولة دي؛ مفيش أداة إضافية اتخصمت.",
                    "warning",
                )

            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except (discord.HTTPException, discord.InteractionResponded):
                logger.warning(
                    "Could not acknowledge roulette weapon action user_id=%s round=%s",
                    interaction.user.id,
                    self.round_num,
                    exc_info=True,
                )
                return

            try:
                success = (
                    self.currency_cog is not None
                    and await asyncio.to_thread(
                        self.currency_cog.remove_item,
                        self.chosen_player.id,
                        "double_shot",
                        1,
                    )
                )
            except Exception:
                logger.exception(
                    "Roulette weapon consumption failed user_id=%s round=%s",
                    self.chosen_player.id,
                    self.round_num,
                )
                return await private_status(
                    interaction,
                    "تعذر تأكيد استخدام السلاح",
                    "حصل عطل أثناء التحقق من الأداة. راجع الشنطة قبل محاولة تانية.",
                    "warning",
                )

            if not success:
                return await private_status(
                    interaction,
                    "تعذر استخدام السلاح",
                    "الأداة مش موجودة في شنطتك أو نظام الاقتصاد مش متاح.",
                    "warning",
                )

            self.double_shot_active = True
            self.weapon_btn.disabled = True
            self._panel_text.content = (
                f"# الجولة {self.round_num} — السلاح اتفعل\n"
                f"{self.chosen_player.mention} فعل سلاح الحفرة. يقدر يختار ضحيتين بدل واحدة."
            )
            if self.message is not None:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    logger.warning(
                        "Roulette weapon was consumed but its public panel could not be refreshed round=%s",
                        self.round_num,
                        exc_info=True,
                    )
            await private_status(
                interaction,
                "تم تفعيل السلاح",
                "اتخصمت أداة واحدة. تقدر تختار ضحيتين في الجولة دي.",
                "success",
            )
