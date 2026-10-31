"""Environment-backed settings for the single-server Casino bot."""
import os

from dotenv import load_dotenv

load_dotenv()


def _read_int_setting(name: str, default: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _read_id_set(name: str, defaults: set[int]) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set(defaults)
    values: set[int] = set()
    for item in raw.split(","):
        try:
            value = int(item.strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.add(value)
    return values


TOKEN = os.getenv("DISCORD_TOKEN", "")
PREFIX = "+"
OWNER_IDS = {1265049672024854559, 798280308071596063}
NEGLAWY_ID = 798280308071596063
BRAND = "Çukur"
REMBG_API_KEY = os.getenv("REMBG_API_KEY", "")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Keep legacy role defaults temporary.
STAFF_ROLE_IDS = [1528494324805927114]

EMOJIS = {
    "check": "<:check:1511996429630181458>",
    "cross": "<:cross:1512824131232465076>",
}

# Enable logs only when configured.
LOG_CHANNELS = {
    "command_errors": _read_int_setting("COMMAND_ERROR_LOG_CHANNEL_ID", 0),
}
SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
EMBED_COLOR = 0x393A41

HOME_GUILD_ID = _read_int_setting("HOME_GUILD_ID") or None
CURRENCY_LOG_CHANNEL_ID = _read_int_setting("CURRENCY_LOG_CHANNEL_ID", 0)
AI_GUILD_ID = _read_int_setting("AI_GUILD_ID", HOME_GUILD_ID or 0) or None
AI_ALLOWED_ROLE_IDS = _read_id_set(
    "AI_ALLOWED_ROLE_IDS",
    {
        1528494324805927114,
        1518803246049919198,
        1512243537435955331,
        1492986771879760137,
        1486759788078432517,
    },
)
AI_ENABLED = os.getenv("AI_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
