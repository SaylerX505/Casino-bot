"""Centralized, configurable permissions and game-channel policy."""
import json
import logging

import discord
from discord.ext import commands

import config
from cogs.casino import bank

logger = logging.getLogger(__name__)

# Canonical game keys are also the persisted channel-setting keys.
GAME_LABELS = {
    "mafia": "المافيا",
    "roulette": "الروليت الروسي",
    "hide_and_seek": "الغميضة",
    "geoguessr": "GeoGuessr",
    "chairs": "الكراسي الموسيقية",
    "wheel": "عجلة الحفرة",
    "rps": "حجرة ورقة مقص",
    "riddle": "الفزورة",
    "coinflip": "رمي العملة",
    "dice": "رمي النرد",
    "slots": "ماكينة الحظ",
    "blackjack": "Blackjack",
}
GAME_COMMAND_ALIASES = {
    "mafia": "mafia",
    "مافيا": "mafia",
    "roulette": "roulette",
    "روليت": "roulette",
    "hide-and-seek": "hide_and_seek",
    "hide_and_seek": "hide_and_seek",
    "غميضه": "hide_and_seek",
    "غميضة": "hide_and_seek",
    "geoguessr": "geoguessr",
    "geo": "geoguessr",
    "جيو": "geoguessr",
    "كراسي": "chairs",
    "chairs": "chairs",
    "wheel": "wheel",
    "عجلة": "wheel",
    "rps": "rps",
    "حومص": "rps",
    "riddle": "riddle",
    "فزورة": "riddle",
    "coinflip": "coinflip",
    "flip": "coinflip",
    "عملة": "coinflip",
    "رمي_العملة": "coinflip",
    "dice": "dice",
    "نرد": "dice",
    "زهر": "dice",
    "slots": "slots",
    "slot": "slots",
    "سلوت": "slots",
    "ماكينة": "slots",
    "blackjack": "blackjack",
    "bj": "blackjack",
    "بلجاك": "blackjack",
    "بلاكجاك": "blackjack",
}


class GameChannelBlocked(commands.CheckFailure):
    """Raised when a game command is used outside its configured channel(s)."""


def staff_role_setting_key(guild_id: int) -> str:
    return f"guild:{int(guild_id)}:staff_role_ids"


def manager_role_setting_key(guild_id: int) -> str:
    return f"guild:{int(guild_id)}:manager_role_ids"


def game_channel_setting_key(guild_id: int, game_key: str) -> str:
    return f"guild:{int(guild_id)}:game_channels:{game_key}"


def log_channel_setting_key(guild_id: int, log_type: str) -> str:
    if log_type not in {"economy", "command_errors"}:
        raise ValueError("Unsupported log-channel setting")
    return f"guild:{int(guild_id)}:log_channels:{log_type}"


def ai_role_setting_key(guild_id: int) -> str:
    return f"guild:{int(guild_id)}:ai_role_ids"


def ai_enabled_setting_key(guild_id: int) -> str:
    return f"guild:{int(guild_id)}:ai_enabled"


def configured_ai_enabled_for_guild(guild_id: int, *, default: bool = True) -> bool:
    """Read this guild's AI override; DB errors fail closed."""
    try:
        raw = bank.get_setting(ai_enabled_setting_key(guild_id))
    except Exception:
        logger.exception("Could not read AI enabled override for guild=%s", guild_id)
        return False
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no", "disabled"}


def configured_log_channel_id(guild_id: int | None, log_type: str, fallback: int = 0) -> int:
    """Resolve a same-guild logging destination. Database failures fail closed."""
    if guild_id is None or log_type not in {"economy", "command_errors"}:
        return 0
    try:
        raw = bank.get_setting(log_channel_setting_key(guild_id, log_type), str(fallback))
    except Exception:
        logger.exception(
            "Could not read log-channel setting guild=%s type=%s",
            guild_id,
            log_type,
        )
        return 0
    if raw is None or str(raw).strip().lower() in {"", "off", "none", "disabled", "0"}:
        return 0
    try:
        channel_id = int(raw)
    except (TypeError, ValueError):
        logger.error("Malformed log-channel setting guild=%s type=%s", guild_id, log_type)
        return 0
    return max(0, channel_id)


def _parse_id_list(raw, *, fallback=None):
    if raw is None:
        return list(fallback or [])
    try:
        values = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(values, list):
        return None
    result = []
    for value in values:
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in result:
            result.append(value)
    return result


def configured_ai_role_ids(guild_id: int) -> set[int]:
    """Return the explicitly configured AI role allowlist for this guild.

    An absent setting retains the host-configured defaults for migration safety;
    an explicit empty list means no extra AI roles. Database errors fail closed.
    """
    try:
        raw = bank.get_setting(ai_role_setting_key(guild_id))
    except Exception:
        logger.exception("Could not read configured AI access roles for guild=%s", guild_id)
        return set()
    values = _parse_id_list(raw, fallback=getattr(config, "AI_ALLOWED_ROLE_IDS", set()))
    if values is None:
        logger.error("Malformed AI role configuration for guild=%s", guild_id)
        return set()
    return set(values)


def configured_staff_role_ids(guild_id: int) -> set[int]:
    """Return configured game-staff roles; unsaved guilds use the legacy defaults."""
    try:
        raw = bank.get_setting(staff_role_setting_key(guild_id))
    except Exception:
        logger.exception("Could not read configured staff roles for guild=%s", guild_id)
        return set()
    values = _parse_id_list(raw, fallback=config.STAFF_ROLE_IDS)
    if values is None:
        logger.error("Malformed staff-role configuration for guild=%s", guild_id)
        return set()
    return set(values)


def has_admin_access(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def configured_manager_role_ids(guild_id: int) -> set[int]:
    """Return roles trusted to open the setup wizard; only Discord admins can edit them."""
    try:
        raw = bank.get_setting(manager_role_setting_key(guild_id))
    except Exception:
        logger.exception("Could not read configured manager roles for guild=%s", guild_id)
        return set()
    values = _parse_id_list(raw, fallback=[])
    if values is None:
        logger.error("Malformed manager-role configuration for guild=%s", guild_id)
        return set()
    return set(values)


def has_staff_access(user) -> bool:
    """Discord admins, configured bot managers, and game-staff roles can host games."""
    if has_admin_access(user):
        return True

    guild = getattr(user, "guild", None)
    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        return False
    role_ids = {
        getattr(role, "id", None)
        for role in getattr(user, "roles", ())
    }
    permitted_roles = configured_staff_role_ids(guild_id)
    permitted_roles.update(configured_manager_role_ids(guild_id))
    return bool(role_ids.intersection(permitted_roles))


def game_key_from_command_name(name: str | None) -> str | None:
    if not name:
        return None
    return GAME_COMMAND_ALIASES.get(str(name).strip().lower())


def _interaction_command_names(interaction: discord.Interaction) -> list[str]:
    names = []
    command = getattr(interaction, "command", None)
    if command is not None:
        names.extend(
            str(value)
            for value in (
                getattr(command, "name", None),
                getattr(command, "qualified_name", None),
            )
            if value
        )

    data = getattr(interaction, "data", None)
    if isinstance(data, dict):
        def collect(node):
            if not isinstance(node, dict):
                return
            name = node.get("name")
            if isinstance(name, str):
                names.append(name)
            for option in node.get("options", ()) or ():
                collect(option)
        collect(data)
    return names


def game_key_from_interaction(interaction: discord.Interaction) -> str | None:
    for name in reversed(_interaction_command_names(interaction)):
        game_key = game_key_from_command_name(name)
        if game_key:
            return game_key
    return None


def is_game_channel_allowed(guild_id: int | None, channel_id: int | None, game_key: str) -> bool:
    """Missing/'all' policy means unrestricted; a configured list is an allowlist."""
    if guild_id is None or channel_id is None or game_key not in GAME_LABELS:
        return True
    try:
        raw = bank.get_setting(game_channel_setting_key(guild_id, game_key), "all")
    except Exception:
        logger.exception(
            "Could not read channel policy guild=%s game=%s",
            guild_id,
            game_key,
        )
        return False

    if raw is None or str(raw).strip().lower() == "all":
        return True
    channel_ids = _parse_id_list(raw)
    if channel_ids is None:
        logger.error("Malformed channel policy guild=%s game=%s", guild_id, game_key)
        return False
    return int(channel_id) in set(channel_ids)


def is_staff_or_admin():
    async def predicate(ctx):
        return has_staff_access(ctx.author)

    return commands.check(predicate)
