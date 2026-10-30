"""Shared authorization, configuration readers, and base layout for the setup wizard."""

import logging

import discord
from discord.ui import LayoutView

import config
from cogs.casino import bank
from cogs.casino.permissions import (
    _parse_id_list,
    configured_ai_enabled_for_guild,
    configured_log_channel_id,
    configured_manager_role_ids,
    games_channel_setting_key,
    has_admin_access,
    manager_role_setting_key,
    staff_role_setting_key,
)
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


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


def _games_channel_policy(guild_id: int) -> tuple[bool, int | None]:
    """Read the one optional channel shared by every game in this guild."""
    try:
        raw = bank.get_setting(games_channel_setting_key(guild_id), "all")
    except Exception:
        logger.exception("Wizard failed to read global game-channel policy guild=%s", guild_id)
        return False, None
    if raw is None or str(raw).strip().lower() == "all":
        return True, None
    try:
        channel_id = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.error("Wizard found malformed global game-channel policy guild=%s", guild_id)
        return False, None
    if channel_id <= 0:
        logger.error("Wizard found invalid global game-channel policy guild=%s", guild_id)
        return False, None
    return False, channel_id

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
    return configured_ai_enabled_for_guild(guild_id, default=True)


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

    def _button(self, label, custom_id, callback):
        button = discord.ui.Button(
            label=label,
            custom_id=custom_id,
            style=discord.ButtonStyle.secondary,
        )
        button.callback = callback
        return button

    def _notice_text(self):
        return f"\n\n> {self.notice}" if self.notice else ""
