"""Interactive, private setup wizard for the single-server game bot."""
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import (
    ActionRow,
    ChannelSelect,
    Container,
    LayoutView,
    RoleSelect,
    Select,
    Separator,
    TextDisplay,
)

import config
from cogs.casino import bank
from cogs.casino.permissions import (
    GAME_LABELS,
    _parse_id_list,
    ai_enabled_setting_key,
    ai_role_setting_key,
    configured_ai_role_ids,
    configured_log_channel_id,
    is_ai_enabled_for_guild as guild_ai_enabled,
    configured_manager_role_ids,
    game_channel_setting_key,
    has_admin_access,
    log_channel_setting_key,
    manager_role_setting_key,
    staff_role_setting_key,
)
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)

GAME_ORDER = (
    "mafia",
    "roulette",
    "hide_and_seek",
    "geoguessr",
    "chairs",
    "wheel",
    "rps",
    "riddle",
)


def _configured_roles(guild_id: int) -> list[int]:
    try:
        raw = bank.get_setting(staff_role_setting_key(guild_id))
        values = _parse_id_list(raw, fallback=config.STAFF_ROLE_IDS)
        return values or []
    except Exception:
        logger.exception("Wizard failed to read staff-role settings for guild=%s", guild_id)
        return []


def _configured_manager_roles(guild_id: int) -> list[int]:
    try:
        raw = bank.get_setting(manager_role_setting_key(guild_id))
        values = _parse_id_list(raw, fallback=[])
        return values or []
    except Exception:
        logger.exception("Wizard failed to read manager roles for guild=%s", guild_id)
        return []


def _is_discord_admin(interaction: discord.Interaction) -> bool:
    return bool(
        interaction.guild is not None
        and (
            interaction.user.id == interaction.guild.owner_id
            or has_admin_access(interaction.user)
        )
    )


def _can_manage_wizard(interaction: discord.Interaction) -> bool:
    if _is_discord_admin(interaction):
        return True
    guild_id = getattr(interaction, "guild_id", None)
    if guild_id is None:
        return False
    role_ids = {
        getattr(role, "id", None)
        for role in getattr(interaction.user, "roles", ())
    }
    return bool(role_ids.intersection(configured_manager_role_ids(guild_id)))


def _channel_policy(guild_id: int, game_key: str) -> tuple[bool, list[int]]:
    try:
        raw = bank.get_setting(game_channel_setting_key(guild_id, game_key), "all")
    except Exception:
        logger.exception(
            "Wizard failed to read channel policy guild=%s game=%s",
            guild_id,
            game_key,
        )
        return False, []
    if raw is None or str(raw).strip().lower() == "all":
        return True, []
    values = _parse_id_list(raw)
    return (False, values or []) if values is not None else (False, [])


LOG_TYPE_LABELS = {
    "economy": "سجلات الاقتصاد",
    "command_errors": "أخطاء الأوامر",
}


def _log_channel_fallback(log_type: str) -> int:
    if log_type == "economy":
        return int(getattr(config, "CURRENCY_LOG_CHANNEL_ID", 0) or 0)
    return int(config.LOG_CHANNELS.get("command_errors", 0) or 0)


def _configured_log_channel(guild_id: int, log_type: str) -> int:
    return configured_log_channel_id(
        guild_id,
        log_type,
        _log_channel_fallback(log_type),
    )


def _global_ai_enabled() -> bool:
    default = bool(getattr(config, "AI_ENABLED", True))
    try:
        raw = bank.get_setting("ai_enabled", "true" if default else "false")
    except Exception:
        logger.exception("Could not read global AI enabled setting for the wizard")
        return False
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _guild_ai_enabled(guild_id: int) -> bool:
    return guild_ai_enabled(guild_id, default=True)


def _configured_ai_enabled(guild_id: int | None = None) -> bool:
    global_enabled = _global_ai_enabled()
    return global_enabled if guild_id is None else global_enabled and _guild_ai_enabled(guild_id)

def _authorized(interaction: discord.Interaction, owner_id: int) -> bool:
    return bool(
        interaction.user.id == owner_id
        and interaction.guild is not None
        and interaction.guild_id is not None
    )


async def _guard_interaction(interaction: discord.Interaction, owner_id: int) -> bool:
    if _authorized(interaction, owner_id):
        return True
    await interaction.response.send_message(
        "المعالج ده خاص بالمسؤول اللي فتحه. افتح أمر wizard من حسابك لوحدك.",
        ephemeral=True,
    )
    return False


class WizardLayout(LayoutView):
    def __init__(self, guild: discord.Guild, owner: discord.abc.User, *, notice: str | None = None):
        super().__init__(timeout=240)
        self.guild_id = guild.id
        self.owner_id = owner.id
        self.message = None
        self.notice = notice

    async def _edit_message(self, interaction: discord.Interaction, **kwargs):
        """Keep the currently displayed message bound to every replacement wizard view."""
        next_view = kwargs.get("view")
        if isinstance(next_view, WizardLayout):
            next_view.message = interaction.message
        return await interaction.response.edit_message(**kwargs)

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(
                    view=StatusPanel(
                        "انتهى وقت الإعداد",
                        "انتهت جلسة المعالج. شغّل أمر wizard لفتح جلسة جديدة.",
                        "neutral",
                    )
                )
            except discord.HTTPException:
                logger.debug("Could not close expired wizard view", exc_info=True)

    def _button(self, label, custom_id, callback, *, emoji=None):
        button = discord.ui.Button(
            label=label,
            custom_id=custom_id,
            style=discord.ButtonStyle.secondary,
            emoji=emoji,
        )
        button.callback = callback
        return button

    def _notice_text(self):
        return f"\n\n> {self.notice}" if self.notice else ""


class WizardHomeView(WizardLayout):
    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        roles = _configured_roles(guild.id)
        manager_roles = _configured_manager_roles(guild.id)
        restricted = []
        for key in GAME_ORDER:
            unrestricted, allowed_channel_ids = _channel_policy(guild.id, key)
            if unrestricted:
                continue
            if not allowed_channel_ids:
                restricted.append(f"{GAME_LABELS[key]} (متوقفة)")
            else:
                restricted.append(GAME_LABELS[key])
        role_summary = ", ".join(f"<@&{role_id}>" for role_id in roles) or (
            "لا توجد رولات ستاف مخصصة؛ Administrator يقدر يشغل الألعاب."
        )
        manager_summary = ", ".join(f"<@&{role_id}>" for role_id in manager_roles) or (
            "المالك وAdministrator فقط."
        )
        channel_summary = ", ".join(restricted) or "كل الألعاب مسموح بها في كل القنوات."
        text = (
            "# Çukur • إعداد البوت\n"
            "معالج إعداد خاص ومتدرج. كل تغيير بيتحفظ في قاعدة البيانات فور تأكيده، "
            "ومش محتاج تعيد تشغيل البوت.\n\n"
            f"**رولات إدارة البوت:** {manager_summary}\n"
            f"**رولات الستاف:** {role_summary}\n"
            f"**قيود القنوات:** {channel_summary}\n\n"
            "رولات الإدارة تقدر تفتح المعالج وتعدل إعدادات الألعاب؛ "
            "تغيير رولات الإدارة نفسها للـAdministrator فقط."
            + self._notice_text()
        )
        self.add_item(
            Container(
                TextDisplay(text[:3900]),
                Separator(),
                ActionRow(
                    self._button(
                        "رولات الإدارة",
                        "wizard_manager_roles",
                        self.open_manager_roles,
                        emoji="🔐",
                    ),
                    self._button(
                        "رولات الستاف",
                        "wizard_staff_roles",
                        self.open_roles,
                        emoji="🛡️",
                    ),
                    self._button(
                        "قنوات الألعاب",
                        "wizard_game_channels",
                        self.open_channels,
                        emoji="🎮",
                    ),
                ),
                ActionRow(
                    self._button(
                        "السجلات",
                        "wizard_logs",
                        self.open_logs,
                        emoji="📋",
                    ),
                    self._button(
                        "الذكاء الاصطناعي",
                        "wizard_ai_settings",
                        self.open_ai_settings,
                        emoji="🤖",
                    ),
                    self._button(
                        "مراجعة الإعدادات",
                        "wizard_review",
                        self.open_review,
                        emoji="⚙️",
                    ),
                    self._button(
                        "إغلاق المعالج",
                        "wizard_close",
                        self.close_wizard,
                        emoji="✕",
                    ),
                ),
                accent_color=0x393A41,
            )
        )

    async def open_manager_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        if not _is_discord_admin(interaction):
            return await interaction.response.send_message(
                "تغيير رولات إدارة البوت متاح للمالك أو Administrator فقط.",
                ephemeral=True,
            )
        await self._edit_message(interaction,
            view=AdminRoleWizardView(interaction.guild, interaction.user)
        )

    async def open_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=StaffRoleWizardView(interaction.guild, interaction.user)
        )

    async def open_channels(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=GameChannelWizardView(interaction.guild, interaction.user)
        )

    async def open_logs(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=LogChannelWizardView(interaction.guild, interaction.user)
        )

    async def open_ai_settings(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(interaction.guild, interaction.user),
        )

    async def open_review(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=WizardReviewView(interaction.guild, interaction.user)
        )

    async def close_wizard(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=StatusPanel(
                "تم إغلاق المعالج",
                "اتقفلت جلسة الإعداد. الإعدادات اللي تم حفظها بالفعل فضلت محفوظة.",
                "success",
            )
        )


class AdminRoleWizardView(WizardLayout):
    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        current_roles = _configured_manager_roles(guild.id)
        role_text = ", ".join(f"<@&{role_id}>" for role_id in current_roles)
        if not role_text:
            role_text = "مفيش رولات إدارة مخصصة؛ الإعدادات للمالك وAdministrator فقط."
        self.role_select = RoleSelect(
            placeholder="اختار الرولات الموثوقة لإدارة إعدادات البوت",
            min_values=1,
            max_values=25,
        )
        self.role_select.callback = self.save_roles
        self.add_item(
            Container(
                TextDisplay(
                    "# رولات إدارة البوت\n"
                    "الأعضاء اللي معاهم الرولات دي يقدروا يفتحوا /wizard ويعدلوا إعدادات تشغيل الألعاب والقنوات. "
                    "تغيير رولات الإدارة نفسها يفضل للمالك أو Administrator فقط.\n\n"
                    f"**الرولات الحالية:** {role_text}\n\n"
                    "الرولات دي لا تكتسب Administrator أو أي صلاحية Discord أخرى."
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(self.role_select),
                ActionRow(
                    self._button("مسح رولات الإدارة", "wizard_clear_manager_roles", self.clear_roles),
                    self._button("رجوع", "wizard_manager_back", self.go_home),
                ),
                accent_color=0x8B1E1E,
            )
        )

    async def save_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        if not _is_discord_admin(interaction):
            return await interaction.response.send_message(
                "تغيير رولات إدارة البوت متاح للمالك أو Administrator فقط.",
                ephemeral=True,
            )
        role_ids = sorted({
            role.id
            for role in self.role_select.values
            if role.id != self.guild_id
            and getattr(role, "guild", None) is not None
            and role.guild.id == self.guild_id
        })
        if not role_ids:
            return await interaction.response.send_message(
                "اختار رول واحدة على الأقل، أو استخدم زر مسح رولات الإدارة.",
                ephemeral=True,
            )
        try:
            bank.set_setting(
                manager_role_setting_key(self.guild_id),
                json.dumps(role_ids),
            )
        except Exception:
            logger.exception("Failed to persist manager roles for guild=%s", self.guild_id)
            return await interaction.response.send_message(
                "تعذر حفظ الرولات. مفيش تغيير اتأكد حفظه.",
                ephemeral=True,
            )
        logger.info(
            "Wizard updated manager roles guild=%s actor=%s role_count=%s",
            self.guild_id,
            interaction.user.id,
            len(role_ids),
        )
        await self._edit_message(interaction,
            view=AdminRoleWizardView(
                interaction.guild,
                interaction.user,
                notice=f"اتحفظت {len(role_ids)} رول إدارة. تقدر تفتح المعالج بيها من غير Administrator.",
            )
        )

    async def clear_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        if not _is_discord_admin(interaction):
            return await interaction.response.send_message(
                "مسح رولات إدارة البوت متاح للمالك أو Administrator فقط.",
                ephemeral=True,
            )
        try:
            bank.set_setting(manager_role_setting_key(self.guild_id), "[]")
        except Exception:
            logger.exception("Failed to clear manager roles for guild=%s", self.guild_id)
            return await interaction.response.send_message(
                "تعذر حفظ التغيير. جرّب تاني بعد إصلاح قاعدة البيانات.",
                ephemeral=True,
            )
        logger.info("Wizard cleared manager roles guild=%s actor=%s", self.guild_id, interaction.user.id)
        await self._edit_message(interaction,
            view=AdminRoleWizardView(
                interaction.guild,
                interaction.user,
                notice="اتمسحت رولات الإدارة المخصصة. المعالج للمالك وAdministrator فقط دلوقتي.",
            )
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=WizardHomeView(interaction.guild, interaction.user)
        )


class StaffRoleWizardView(WizardLayout):
    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        current_roles = _configured_roles(guild.id)
        role_text = ", ".join(f"<@&{role_id}>" for role_id in current_roles)
        if not role_text:
            role_text = "لا توجد رولات ستاف مخصصة حاليًا."
        self.role_select = RoleSelect(
            placeholder="اختار رولات الستاف المسموح لها بتشغيل الألعاب",
            min_values=1,
            max_values=25,
        )
        self.role_select.callback = self.save_roles
        self.add_item(
            Container(
                TextDisplay(
                    "# صلاحيات تشغيل الألعاب\n"
                    "اختار الرولات اللي تقدر تبدأ الألعاب وتستخدم صلاحيات الستاف. "
                    "ده لا يمنح أي صلاحية Discord جديدة، وAdministrator يظل مخوّلًا دائمًا.\n\n"
                    f"**الرولات الحالية:** {role_text}\n\n"
                    "اختيار جديد يستبدل القائمة الحالية. لإزالة كل الرولات المخصصة استخدم زر المسح."
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(self.role_select),
                ActionRow(
                    self._button("مسح رولات الستاف", "wizard_clear_staff_roles", self.clear_roles),
                    self._button("رجوع", "wizard_staff_back", self.go_home),
                ),
                accent_color=0x2AA6A4,
            )
        )

    async def save_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        role_ids = sorted({
            role.id
            for role in self.role_select.values
            if role.id != self.guild_id and getattr(role, "guild", None) is not None
            and role.guild.id == self.guild_id
        })
        if not role_ids:
            return await interaction.response.send_message(
                "اختار رول واحدة على الأقل، أو استخدم زر مسح رولات الستاف.",
                ephemeral=True,
            )
        try:
            bank.set_setting(staff_role_setting_key(self.guild_id), json.dumps(role_ids))
        except Exception:
            logger.exception("Failed to persist staff roles for guild=%s", self.guild_id)
            return await interaction.response.send_message(
                "تعذر حفظ الرولات في قاعدة البيانات. مفيش تغيير اتأكد حفظه.",
                ephemeral=True,
            )
        logger.info(
            "Wizard updated staff roles guild=%s actor=%s role_count=%s",
            self.guild_id,
            interaction.user.id,
            len(role_ids),
        )
        await self._edit_message(interaction,
            view=StaffRoleWizardView(
                interaction.guild,
                interaction.user,
                notice=f"اتحفظت {len(role_ids)} رول ستاف بنجاح.",
            )
        )

    async def clear_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        try:
            bank.set_setting(staff_role_setting_key(self.guild_id), "[]")
        except Exception:
            logger.exception("Failed to clear staff roles for guild=%s", self.guild_id)
            return await interaction.response.send_message(
                "تعذر حفظ التغيير. جرّب تاني بعد إصلاح قاعدة البيانات.",
                ephemeral=True,
            )
        logger.info(
            "Wizard cleared staff roles guild=%s actor=%s",
            self.guild_id,
            interaction.user.id,
        )
        await self._edit_message(interaction,
            view=StaffRoleWizardView(
                interaction.guild,
                interaction.user,
                notice="اتمسحت رولات الستاف المخصصة. حسابات Administrator فقط تقدر تبدأ الألعاب الآن.",
            )
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=WizardHomeView(interaction.guild, interaction.user))


class GameChannelWizardView(WizardLayout):
    def __init__(self, guild, owner, *, selected_game="mafia", notice=None):
        super().__init__(guild, owner, notice=notice)
        self.selected_game = selected_game if selected_game in GAME_LABELS else "mafia"
        unrestricted, channel_ids = _channel_policy(guild.id, self.selected_game)
        current = "كل القنوات" if unrestricted else (
            ", ".join(f"<#{channel_id}>" for channel_id in channel_ids) or "لا توجد قنوات مسموحة"
        )
        options = [
            discord.SelectOption(
                label=GAME_LABELS[key],
                value=key,
                default=key == self.selected_game,
            )
            for key in GAME_ORDER
        ]
        self.game_select = Select(
            placeholder="اختار اللعبة اللي هتظبط قنواتها",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.game_select.callback = self.choose_game
        self.channel_select = ChannelSelect(
            placeholder="اختار القنوات المسموح فيها اللعبة دي",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=25,
        )
        self.channel_select.callback = self.save_channels
        self.add_item(
            Container(
                TextDisplay(
                    "# قنوات الألعاب\n"
                    "حدد لعبة من القائمة، وبعدها اختار القنوات اللي مسموح تشغيلها فيها. "
                    "إعداد كل لعبة مستقل عن الباقي.\n\n"
                    f"**اللعبة الحالية:** {GAME_LABELS[self.selected_game]}\n"
                    f"**السماح الحالي:** {current}\n\n"
                    "حفظ اختيار قنوات جديد يقفل اللعبة على القنوات المختارة فقط. "
                    "زر «كل القنوات» يلغي القيد للعبة المحددة."
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(self.game_select),
                ActionRow(self.channel_select),
                ActionRow(
                    self._button(
                        "كل القنوات",
                        f"wizard_channels_all_{self.selected_game}",
                        self.allow_all_channels,
                    ),
                    self._button(
                        "تعطيل اللعبة",
                        f"wizard_channels_disable_{self.selected_game}",
                        self.disable_game,
                    ),
                    self._button("رجوع", "wizard_channels_back", self.go_home),
                ),
                accent_color=0x2AA6A4,
            )
        )

    async def choose_game(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        selected_game = self.game_select.values[0]
        await self._edit_message(interaction,
            view=GameChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_game=selected_game,
            )
        )

    async def save_channels(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        channel_ids = sorted({
            channel.id
            for channel in self.channel_select.values
            if getattr(channel, "guild", None) is not None
            and channel.guild.id == self.guild_id
        })
        if not channel_ids:
            return await interaction.response.send_message(
                "اختار قناة واحدة على الأقل.",
                ephemeral=True,
            )
        try:
            bank.set_setting(
                game_channel_setting_key(self.guild_id, self.selected_game),
                json.dumps(channel_ids),
            )
        except Exception:
            logger.exception(
                "Failed to persist game channel policy guild=%s game=%s",
                self.guild_id,
                self.selected_game,
            )
            return await interaction.response.send_message(
                "تعذر حفظ القنوات. مفيش تغيير اتأكد حفظه.",
                ephemeral=True,
            )
        logger.info(
            "Wizard updated game channels guild=%s game=%s actor=%s channel_count=%s",
            self.guild_id,
            self.selected_game,
            interaction.user.id,
            len(channel_ids),
        )
        await self._edit_message(interaction,
            view=GameChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_game=self.selected_game,
                notice=f"اتحفظت {len(channel_ids)} قناة للعبة {GAME_LABELS[self.selected_game]}.",
            )
        )

    async def allow_all_channels(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        try:
            bank.set_setting(game_channel_setting_key(self.guild_id, self.selected_game), "all")
        except Exception:
            logger.exception(
                "Failed to reset game channel policy guild=%s game=%s",
                self.guild_id,
                self.selected_game,
            )
            return await interaction.response.send_message(
                "تعذر حفظ إعداد القنوات. جرّب تاني.",
                ephemeral=True,
            )
        logger.info(
            "Wizard set all-channel policy guild=%s game=%s actor=%s",
            self.guild_id,
            self.selected_game,
            interaction.user.id,
        )
        await self._edit_message(interaction,
            view=GameChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_game=self.selected_game,
                notice=f"اللعبة {GAME_LABELS[self.selected_game]} بقت متاحة في كل القنوات.",
            )
        )

    async def disable_game(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        try:
            bank.set_setting(
                game_channel_setting_key(self.guild_id, self.selected_game),
                "[]",
            )
        except Exception:
            logger.exception(
                "Failed to disable game guild=%s game=%s",
                self.guild_id,
                self.selected_game,
            )
            return await interaction.response.send_message(
                "تعذر حفظ إعداد تعطيل اللعبة. مفيش تغيير اتأكد حفظه.",
                ephemeral=True,
            )
        logger.info(
            "Wizard disabled game guild=%s game=%s actor=%s",
            self.guild_id,
            self.selected_game,
            interaction.user.id,
        )
        await self._edit_message(interaction,
            view=GameChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_game=self.selected_game,
                notice=f"تم تعطيل {GAME_LABELS[self.selected_game]}. تقدر تعيد تفعيلها من زر كل القنوات أو بتحديد قنوات.",
            )
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=WizardHomeView(interaction.guild, interaction.user))


class LogChannelWizardView(WizardLayout):
    """Configure per-guild audit destinations without hard-coded cross-server channels."""

    def __init__(self, guild, owner, *, selected_type="economy", notice=None):
        super().__init__(guild, owner, notice=notice)
        self.selected_type = selected_type if selected_type in LOG_TYPE_LABELS else "economy"
        current_id = _configured_log_channel(guild.id, self.selected_type)
        current = f"<#{current_id}>" if current_id else "متوقف"

        self.type_select = Select(
            placeholder="اختار نوع السجل",
            options=[
                discord.SelectOption(
                    label=label,
                    value=key,
                    default=key == self.selected_type,
                )
                for key, label in LOG_TYPE_LABELS.items()
            ],
            min_values=1,
            max_values=1,
        )
        self.type_select.callback = self.choose_type
        self.channel_select = ChannelSelect(
            placeholder="اختار قناة لاستقبال السجلات",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )
        self.channel_select.callback = self.save_channel

        self.add_item(
            Container(
                TextDisplay(
                    "# إعدادات السجلات\n"
                    "اختار قناة مستقلة لسجلات الاقتصاد وأخطاء الأوامر. "
                    "السجلات لا تُرسل إلا لقناة داخل السيرفر نفسه.\n\n"
                    f"**نوع السجل:** {LOG_TYPE_LABELS[self.selected_type]}\n"
                    f"**القناة الحالية:** {current}\n\n"
                    "اختيار قناة جديدة يحفظ التغيير فورًا. تقدر توقف النوع المحدد من الزر بالأسفل."
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(self.type_select),
                ActionRow(self.channel_select),
                ActionRow(
                    self._button("إيقاف هذا السجل", "wizard_logs_disable", self.disable_log),
                    self._button("رجوع", "wizard_logs_back", self.go_home),
                ),
                accent_color=0x393A41,
            )
        )

    async def choose_type(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        selected = self.type_select.values[0]
        await self._edit_message(interaction,
            view=LogChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_type=selected,
            )
        )

    async def save_channel(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        channel = next(
            (
                value for value in self.channel_select.values
                if getattr(getattr(value, "guild", None), "id", None) == self.guild_id
            ),
            None,
        )
        if channel is None:
            return await interaction.response.send_message(
                "اختار قناة نصية من نفس السيرفر.",
                ephemeral=True,
            )

        bot_member = getattr(interaction.guild, "me", None)
        if bot_member is not None:
            permissions = channel.permissions_for(bot_member)
            if not getattr(permissions, "view_channel", False) or not getattr(permissions, "send_messages", False):
                return await interaction.response.send_message(
                    "البوت محتاج صلاحية عرض القناة وإرسال الرسائل فيها قبل حفظها كسجل.",
                    ephemeral=True,
                )

        try:
            bank.set_setting(
                log_channel_setting_key(self.guild_id, self.selected_type),
                str(channel.id),
            )
        except Exception:
            logger.exception(
                "Failed to persist log channel guild=%s type=%s",
                self.guild_id,
                self.selected_type,
            )
            return await interaction.response.send_message(
                "تعذر حفظ قناة السجل. مفيش تغيير اتأكد حفظه.",
                ephemeral=True,
            )

        logger.info(
            "Wizard updated log channel guild=%s type=%s actor=%s channel_id=%s",
            self.guild_id,
            self.selected_type,
            interaction.user.id,
            channel.id,
        )
        await self._edit_message(interaction,
            view=LogChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_type=self.selected_type,
                notice=f"اتحفظت قناة {LOG_TYPE_LABELS[self.selected_type]}: <#{channel.id}>.",
            )
        )

    async def disable_log(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        try:
            bank.set_setting(
                log_channel_setting_key(self.guild_id, self.selected_type),
                "0",
            )
        except Exception:
            logger.exception(
                "Failed to disable log channel guild=%s type=%s",
                self.guild_id,
                self.selected_type,
            )
            return await interaction.response.send_message(
                "تعذر حفظ إعداد إيقاف السجل. جرّب تاني.",
                ephemeral=True,
            )

        logger.info(
            "Wizard disabled log channel guild=%s type=%s actor=%s",
            self.guild_id,
            self.selected_type,
            interaction.user.id,
        )
        await self._edit_message(interaction,
            view=LogChannelWizardView(
                interaction.guild,
                interaction.user,
                selected_type=self.selected_type,
                notice=f"تم إيقاف {LOG_TYPE_LABELS[self.selected_type]}.",
            )
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=WizardHomeView(interaction.guild, interaction.user)
        )


class AISettingsWizardView(WizardLayout):
    """Manage the optional AI responder and its per-guild role allowlist."""

    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        enabled = _guild_ai_enabled(guild.id)
        global_enabled = _global_ai_enabled()
        roles = sorted(configured_ai_role_ids(guild.id))
        role_summary = ", ".join(f"<@&{role_id}>" for role_id in roles) or "لا توجد رولات إضافية"
        api_state = "مفتاح API مضبوط" if config.DEEPSEEK_API_KEY else "مفتاح API غير مضبوط"
        state = (
            "شغال"
            if global_enabled and enabled
            else ("متوقف من الإعداد العام" if not global_enabled else "متوقف في السيرفر")
        )

        self.role_select = RoleSelect(
            placeholder="اختار رولات مسموح لها باستخدام محسن",
            min_values=1,
            max_values=25,
        )
        self.role_select.callback = self.save_roles
        toggle_label = "إيقاف محسن في السيرفر" if enabled else "تشغيل محسن في السيرفر"

        self.add_item(
            Container(
                TextDisplay(
                    "# إعدادات محسن\n"
                    f"**الحالة الفعلية:** {state}\n"
                    f"**المفتاح العام:** {'مسموح' if global_enabled else 'متوقف من الإعداد العام'}\n"
                    f"**التكامل:** {api_state}\n"
                    f"**رولات الوصول:** {role_summary}\n\n"
                    "مالك السيرفر والـBoosters لهم وصول دائم. الرولات هنا تضيف أعضاء مسموح لهم. "
                    "اختيار رولات جديدة يحفظ القائمة فورًا، ومسحها يرجع لقواعد الوصول الأساسية.\n"
                    "تشغيل محسن يحتاج مفتاح DeepSeek مضبوطًا في إعدادات الاستضافة."
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(self.role_select),
                ActionRow(
                    self._button(toggle_label, "wizard_ai_toggle", self.toggle_ai),
                    self._button("مسح رولات الوصول", "wizard_ai_clear_roles", self.clear_roles),
                    self._button("رجوع", "wizard_ai_back", self.go_home),
                ),
                accent_color=0x393A41,
            )
        )

    async def save_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        role_ids = sorted({
            int(role.id)
            for role in self.role_select.values
            if getattr(getattr(role, "guild", None), "id", None) == self.guild_id
            and int(role.id) != self.guild_id
        })
        if not role_ids:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "اختيار غير صالح",
                    "اختار رولة واحدة على الأقل من رولات السيرفر. استخدم زر مسح رولات الوصول لو عايز القائمة الإضافية تبقى فاضية.",
                    "warning",
                ),
                ephemeral=True,
            )

        try:
            bank.set_setting(ai_role_setting_key(self.guild_id), json.dumps(role_ids))
        except Exception:
            logger.exception(
                "Failed to persist AI role allowlist guild=%s",
                self.guild_id,
            )
            return await interaction.response.send_message(
                view=StatusPanel("تعذر حفظ الرولات", "مفيش تغيير اتأكد حفظه.", "warning"),
                ephemeral=True,
            )

        logger.info(
            "Wizard updated AI role allowlist guild=%s actor=%s role_count=%s",
            self.guild_id,
            interaction.user.id,
            len(role_ids),
        )
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(
                interaction.guild,
                interaction.user,
                notice=f"اتحفظت {len(role_ids)} رولة للوصول إلى محسن.",
            ),
        )

    async def clear_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        try:
            bank.set_setting(ai_role_setting_key(self.guild_id), "[]")
        except Exception:
            logger.exception("Failed to clear AI role allowlist guild=%s", self.guild_id)
            return await interaction.response.send_message(
                view=StatusPanel("تعذر مسح الرولات", "مفيش تغيير اتأكد حفظه.", "warning"),
                ephemeral=True,
            )

        logger.info(
            "Wizard cleared AI role allowlist guild=%s actor=%s",
            self.guild_id,
            interaction.user.id,
        )
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(
                interaction.guild,
                interaction.user,
                notice="تم مسح الرولات الإضافية؛ المالك والـBoosters ما زالوا مسموحين.",
            ),
        )

    async def toggle_ai(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        enabled = not _guild_ai_enabled(self.guild_id)
        ai_cog = interaction.client.get_cog("AI")
        if enabled and not _global_ai_enabled():
            return await interaction.response.send_message(
                view=StatusPanel(
                    "محسن متوقف من الإعداد العام",
                    "تفعيل محسن بشكل عام متاح لمالك التطبيق من /admin ai-toggle. تقدر تضبط تفعيل السيرفر هنا بعد تشغيل المفتاح العام.",
                    "warning",
                ),
                ephemeral=True,
            )
        if enabled and not config.DEEPSEEK_API_KEY:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "مفتاح API ناقص",
                    "مش هنشغّل محسن قبل ما تضيف DEEPSEEK_API_KEY في إعدادات الاستضافة.",
                    "warning",
                ),
                ephemeral=True,
            )
        if enabled and ai_cog is None:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "محسن غير متاح",
                    "إضافة الذكاء الاصطناعي مش محملة حاليًا؛ مفيش تغيير اتعمل.",
                    "warning",
                ),
                ephemeral=True,
            )

        try:
            bank.set_setting(ai_enabled_setting_key(self.guild_id), "true" if enabled else "false")
        except Exception:
            logger.exception("Failed to persist AI enabled setting guild=%s", self.guild_id)
            return await interaction.response.send_message(
                view=StatusPanel("تعذر حفظ الإعداد", "محسن ما اتغيرتش حالته.", "warning"),
                ephemeral=True,
            )

        logger.info(
            "Wizard toggled AI guild=%s actor=%s enabled=%s",
            self.guild_id,
            interaction.user.id,
            enabled,
        )
        state = "شغال" if enabled else "متوقف"
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(
                interaction.guild,
                interaction.user,
                notice=f"محسن دلوقتي {state}.",
            ),
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(
            interaction,
            view=WizardHomeView(interaction.guild, interaction.user),
        )


class WizardReviewView(WizardLayout):
    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        roles = _configured_roles(guild.id)
        manager_roles = _configured_manager_roles(guild.id)
        role_summary = ", ".join(f"<@&{role_id}>" for role_id in roles) or "لا توجد رولات ستاف مخصصة."
        manager_summary = ", ".join(f"<@&{role_id}>" for role_id in manager_roles) or "المالك وAdministrator فقط."
        lines = [
            f"**رولات إدارة البوت:** {manager_summary}",
            f"**رولات الستاف:** {role_summary}",
            "",
            "**قنوات الألعاب:**",
        ]
        for key in GAME_ORDER:
            unrestricted, channel_ids = _channel_policy(guild.id, key)
            channels = (
                "كل القنوات"
                if unrestricted
                else (", ".join(f"<#{channel_id}>" for channel_id in channel_ids) or "اللعبة متوقفة")
            )
            lines.append(f"• **{GAME_LABELS[key]}:** {channels}")
        economy_log = _configured_log_channel(guild.id, "economy")
        error_log = _configured_log_channel(guild.id, "command_errors")
        ai_roles = sorted(configured_ai_role_ids(guild.id))
        ai_role_summary = ", ".join(f"<@&{role_id}>" for role_id in ai_roles) or "لا توجد رولات إضافية"
        lines.extend(
            [
                "",
                f"**محسن في السيرفر:** {'شغال' if _configured_ai_enabled(guild.id) else 'متوقف'} — {'مفتاح API مضبوط' if config.DEEPSEEK_API_KEY else 'مفتاح API ناقص'}",
                f"**رولات الوصول الإضافية لمحسن:** {ai_role_summary}",
                "",
                "**قنوات السجلات:**",
                f"• **سجلات الاقتصاد:** {f'<#{economy_log}>' if economy_log else 'متوقفة'}",
                f"• **أخطاء الأوامر:** {f'<#{error_log}>' if error_log else 'متوقفة'}",
            ]
        )
        self.add_item(
            Container(
                TextDisplay(
                    "# مراجعة الإعدادات\n"
                    "التغييرات في المعالج تُحفظ مباشرة داخل SQLite. "
                    "إعدادات القنوات مستقلة لكل لعبة.\n\n"
                    + "\n".join(lines)
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(
                    self._button("رولات الإدارة", "wizard_review_managers", self.open_manager_roles),
                    self._button("رولات الستاف", "wizard_review_roles", self.open_roles),
                    self._button("قنوات الألعاب", "wizard_review_channels", self.open_channels),
                    self._button("السجلات", "wizard_review_logs", self.open_logs),
                    self._button("محسن", "wizard_review_ai", self.open_ai_settings),
                ),
                ActionRow(
                    self._button("الرئيسية", "wizard_review_home", self.go_home),
                ),
                accent_color=0x393A41,
            )
        )

    async def open_manager_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        if not _is_discord_admin(interaction):
            return await interaction.response.send_message(
                "تغيير رولات إدارة البوت متاح للمالك أو Administrator فقط.",
                ephemeral=True,
            )
        await self._edit_message(interaction,
            view=AdminRoleWizardView(interaction.guild, interaction.user)
        )

    async def open_roles(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=StaffRoleWizardView(interaction.guild, interaction.user))

    async def open_channels(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=GameChannelWizardView(interaction.guild, interaction.user))

    async def open_logs(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=LogChannelWizardView(interaction.guild, interaction.user)
        )

    async def open_ai_settings(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(interaction.guild, interaction.user),
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=WizardHomeView(interaction.guild, interaction.user))


class SetupWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="wizard",
        description="إعداد رولات الستاف وقنوات كل لعبة خطوة بخطوة",
    )
    @app_commands.guild_only()
    async def wizard(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not _can_manage_wizard(interaction):
            return await interaction.response.send_message(
                "فتح المعالج متاح لمالك السيرفر أو Administrator أو رولات إدارة البوت التي تم إعدادها مسبقًا.",
                ephemeral=True,
            )
        view = WizardHomeView(guild, interaction.user)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(SetupWizard(bot))
