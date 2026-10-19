# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Core Settings 
# Secrets are loaded from .env
TOKEN = os.getenv("DISCORD_TOKEN", "")
PREFIX = "+"
OWNER_IDS = {1265049672024854559, 798280308071596063}
NEGLAWY_ID = 798280308071596063  # المطور
BRAND = "Çukur"
REMBG_API_KEY = os.getenv("REMBG_API_KEY", "")

# AI Settings
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Staff Roles
# allowed to host games
STAFF_ROLE_IDS = [
    1528494324805927114,  
]

# Emojis
EMOJIS = {
    "check": "<:check:1511996429630181458>",
    "cross": "<:cross:1512824131232465076>",
    "ping": "<:ping:1511997619042517013>",
}

# Logging Channels
LOG_CHANNELS = {
    "command_errors": 1486822134109180049,
}


SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
EMBED_COLOR = 0x393a41