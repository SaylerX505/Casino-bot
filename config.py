# --- CONFIGURATION ---
import os
from dotenv import load_dotenv

load_dotenv()

# --- CORE SETTINGS --- 
# --- ENVIRONMENT SECRETS ---
TOKEN = os.getenv("DISCORD_TOKEN", "")
PREFIX = "+"
OWNER_IDS = {1265049672024854559, 798280308071596063}
NEGLAWY_ID = 798280308071596063  # المطور
BRAND = "Çukur"
REMBG_API_KEY = os.getenv("REMBG_API_KEY", "")

# --- AI SETTINGS ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# --- STAFF GAME ACCESS ---
STAFF_ROLE_IDS = [
    1528494324805927114,  
]

# --- UI EMOJIS ---
EMOJIS = {
    "check": "<:check:1511996429630181458>",
    "cross": "<:cross:1512824131232465076>",
}

# --- LOGGING CHANNELS ---
LOG_CHANNELS = {
    "command_errors": 1486822134109180049,
}


SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
EMBED_COLOR = 0x393a41

# --- SINGLE-SERVER OPERATIONS ---
# Optional until the intended Discord server ID is configured in the host environment.
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


HOME_GUILD_ID = _read_int_setting("HOME_GUILD_ID") or None
CURRENCY_LOG_CHANNEL_ID = _read_int_setting("CURRENCY_LOG_CHANNEL_ID", 1513736075120742610)
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
AI_ENABLED = os.getenv("AI_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no"}
