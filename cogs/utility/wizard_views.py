"""Private setup wizard pages, grouped into one view layer."""

import json
import logging

import discord
from discord.ui import (
    ActionRow,
    ChannelSelect,
    Container,
    RoleSelect,
    Select,
    Separator,
    TextDisplay,
)

import config
from cogs.casino import bank
from cogs.casino.permissions import ai_enabled_setting_key, ai_role_setting_key
from cogs.utility import wizard as _wizard
from cogs.utility.theme import StatusPanel
from cogs.utility.wizard_common import (
    LOG_TYPE_LABELS,
    WizardLayout,
    _configured_manager_roles,
    _configured_roles,
    _games_channel_policy,
    games_channel_setting_key,
    manager_role_setting_key,
    staff_role_setting_key,
    log_channel_setting_key,
)

logger = logging.getLogger(__name__)


class WizardHomeView(WizardLayout):
    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        roles = _configured_roles(guild.id)
        manager_roles = _configured_manager_roles(guild.id)
        unrestricted, games_channel_id = _games_channel_policy(guild.id)
        role_summary = ", ".join(f"<@&{role_id}>" for role_id in roles) or (
            "لا توجد رولات ستاف مخصصة؛ Administrator يقدر يشغل الألعاب."
        )
        manager_summary = ", ".join(f"<@&{role_id}>" for role_id in manager_roles) or (
            "المالك وAdministrator فقط."
        )
        if unrestricted:
            channel_summary = "كل الألعاب مسموح بها في كل القنوات."
        elif games_channel_id is not None:
            channel_summary = f"كل الألعاب مقيدة بالقناة <#{games_channel_id}>."
        else:
            channel_summary = "تعذر قراءة إعداد القنوات أو الإعداد غير صالح."
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
                    ),
                    self._button(
                        "رولات الستاف",
                        "wizard_staff_roles",
                        self.open_roles,
                    ),
                    self._button(
                        "قنوات الألعاب",
                        "wizard_game_channels",
                        self.open_channels,
                    ),
                ),
                ActionRow(
                    self._button(
                        "السجلات",
                        "wizard_logs",
                        self.open_logs,
                    ),
                    self._button(
                        "الذكاء الاصطناعي",
                        "wizard_ai_settings",
                        self.open_ai_settings,
                    ),
                    self._button(
                        "مراجعة الإعدادات",
                        "wizard_review",
                        self.open_review,
                    ),
                    self._button(
                        "إغلاق المعالج",
                        "wizard_close",
                        self.close_wizard,
                    ),
                ),
                accent_color=0x393A41,
            )
        )

    async def open_manager_roles(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        if not _wizard._is_discord_admin(interaction):
            return await interaction.response.send_message(
                "تغيير رولات إدارة البوت متاح للمالك أو Administrator فقط.",
                ephemeral=True,
            )
        await self._edit_message(interaction,
            view=AdminRoleWizardView(interaction.guild, interaction.user)
        )

    async def open_roles(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=StaffRoleWizardView(interaction.guild, interaction.user)
        )

    async def open_channels(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=GameChannelWizardView(interaction.guild, interaction.user)
        )

    async def open_logs(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=LogChannelWizardView(interaction.guild, interaction.user)
        )

    async def open_ai_settings(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(interaction.guild, interaction.user),
        )

    async def open_review(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=WizardReviewView(interaction.guild, interaction.user)
        )

    async def close_wizard(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        if not _wizard._is_discord_admin(interaction):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        if not _wizard._is_discord_admin(interaction):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=WizardHomeView(interaction.guild, interaction.user))


class GameChannelWizardView(WizardLayout):
    """Configure one optional channel shared by every game in the guild."""

    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        unrestricted, channel_id = _games_channel_policy(guild.id)
        if unrestricted:
            current = "كل القنوات (بدون تقييد)"
        elif channel_id is not None:
            current = f"<#{channel_id}>"
        else:
            current = "تعذر قراءة الإعداد الحالي"

        self.channel_select = ChannelSelect(
            placeholder="اختار القناة الوحيدة المسموح فيها بكل الألعاب",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )
        self.channel_select.callback = self.save_channel
        self.add_item(
            Container(
                TextDisplay(
                    "# قناة الألعاب العامة\n"
                    "الإعداد ده بيطبق على **كل الألعاب**، الفردية والجماعية. "
                    "اختار قناة واحدة لو عايز تجمع الألعاب فيها، أو اختار «كل القنوات» لإلغاء التقييد.\n\n"
                    f"**الإعداد الحالي:** {current}\n\n"
                    "تغيير الإعداد بيتحفظ فورًا، ومش محتاج إعادة تشغيل."
                    + self._notice_text()
                ),
                Separator(),
                ActionRow(self.channel_select),
                ActionRow(
                    self._button(
                        "كل القنوات",
                        "wizard_channels_all",
                        self.allow_all_channels,
                    ),
                    self._button("رجوع", "wizard_channels_back", self.go_home),
                ),
                accent_color=0x2AA6A4,
            )
        )

    async def save_channel(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        selected = next(
            (
                channel for channel in self.channel_select.values
                if getattr(channel, "guild", None) is not None
                and channel.guild.id == self.guild_id
            ),
            None,
        )
        if selected is None:
            return await interaction.response.send_message(
                "اختار قناة نصية من نفس السيرفر.",
                ephemeral=True,
            )

        bot_member = getattr(interaction.guild, "me", None)
        if bot_member is not None:
            permissions = selected.permissions_for(bot_member)
            required_permissions = (
                "view_channel",
                "send_messages",
                "read_message_history",
                "attach_files",
            )
            missing = [
                permission
                for permission in required_permissions
                if not getattr(permissions, permission, False)
            ]
            if missing:
                return await interaction.response.send_message(
                    "البوت محتاج صلاحيات عرض القناة وإرسال الرسائل وقراءة سجل الرسائل ورفع الملفات "
                    "عشان كل الألعاب تشتغل في القناة المختارة.",
                    ephemeral=True,
                )

        try:
            bank.set_setting(games_channel_setting_key(self.guild_id), str(selected.id))
        except Exception:
            logger.exception(
                "Failed to persist global game channel guild=%s",
                self.guild_id,
            )
            return await interaction.response.send_message(
                "تعذر حفظ قناة الألعاب. مفيش تغيير اتأكد حفظه.",
                ephemeral=True,
            )

        logger.info(
            "Wizard updated global game channel guild=%s actor=%s channel_id=%s",
            self.guild_id,
            interaction.user.id,
            selected.id,
        )
        await self._edit_message(
            interaction,
            view=GameChannelWizardView(
                interaction.guild,
                interaction.user,
                notice=f"اتحفظت قناة الألعاب: <#{selected.id}>. كل الألعاب هتشتغل فيها بس.",
            ),
        )

    async def allow_all_channels(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        try:
            bank.set_setting(games_channel_setting_key(self.guild_id), "all")
        except Exception:
            logger.exception(
                "Failed to reset global game-channel policy guild=%s",
                self.guild_id,
            )
            return await interaction.response.send_message(
                "تعذر حفظ إعداد القنوات. جرّب تاني.",
                ephemeral=True,
            )
        logger.info(
            "Wizard allowed all game channels guild=%s actor=%s",
            self.guild_id,
            interaction.user.id,
        )
        await self._edit_message(
            interaction,
            view=GameChannelWizardView(
                interaction.guild,
                interaction.user,
                notice="كل الألعاب بقت متاحة في كل القنوات.",
            ),
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(
            interaction,
            view=WizardHomeView(interaction.guild, interaction.user),
        )


class LogChannelWizardView(WizardLayout):
    """Configure per-guild audit destinations without hard-coded cross-server channels."""

    def __init__(self, guild, owner, *, selected_type="economy", notice=None):
        super().__init__(guild, owner, notice=notice)
        self.selected_type = selected_type if selected_type in LOG_TYPE_LABELS else "economy"
        current_id = _wizard._configured_log_channel(guild.id, self.selected_type)
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=WizardHomeView(interaction.guild, interaction.user)
        )


class AISettingsWizardView(WizardLayout):
    """Manage the optional AI responder and its per-guild role allowlist."""

    def __init__(self, guild, owner, *, notice=None):
        super().__init__(guild, owner, notice=notice)
        enabled = _wizard._guild_ai_enabled(guild.id)
        global_enabled = _wizard._global_ai_enabled()
        roles = sorted(_wizard.configured_ai_role_ids(guild.id))
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        enabled = not _wizard._guild_ai_enabled(self.guild_id)
        ai_cog = interaction.client.get_cog("AI")
        if enabled and not _wizard._global_ai_enabled():
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
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
        unrestricted, games_channel_id = _games_channel_policy(guild.id)
        channels = (
            "كل القنوات"
            if unrestricted
            else (f"<#{games_channel_id}>" if games_channel_id is not None else "تعذر قراءة الإعداد")
        )
        lines.append(f"• **جميع الألعاب:** {channels}")
        economy_log = _wizard._configured_log_channel(guild.id, "economy")
        error_log = _wizard._configured_log_channel(guild.id, "command_errors")
        ai_roles = sorted(_wizard.configured_ai_role_ids(guild.id))
        ai_role_summary = ", ".join(f"<@&{role_id}>" for role_id in ai_roles) or "لا توجد رولات إضافية"
        lines.extend(
            [
                "",
                f"**محسن في السيرفر:** {'شغال' if _wizard._configured_ai_enabled(guild.id) else 'متوقف'} — {'مفتاح API مضبوط' if config.DEEPSEEK_API_KEY else 'مفتاح API ناقص'}",
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
                    "إعداد القنوات عام وبيطبق على كل الألعاب.\n\n"
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
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        if not _wizard._is_discord_admin(interaction):
            return await interaction.response.send_message(
                "تغيير رولات إدارة البوت متاح للمالك أو Administrator فقط.",
                ephemeral=True,
            )
        await self._edit_message(interaction,
            view=AdminRoleWizardView(interaction.guild, interaction.user)
        )

    async def open_roles(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=StaffRoleWizardView(interaction.guild, interaction.user))

    async def open_channels(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=GameChannelWizardView(interaction.guild, interaction.user))

    async def open_logs(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,
            view=LogChannelWizardView(interaction.guild, interaction.user)
        )

    async def open_ai_settings(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(
            interaction,
            view=AISettingsWizardView(interaction.guild, interaction.user),
        )

    async def go_home(self, interaction: discord.Interaction):
        if not await _wizard._guard_interaction(interaction, self.owner_id):
            return
        await self._edit_message(interaction,view=WizardHomeView(interaction.guild, interaction.user))
