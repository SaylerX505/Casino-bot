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
    "prison":      "<:prison:1511994753489702934>",
    "pardon":      "",
    "ban":         "<:ban:1511995178414637116>",
    "kick":        "<:kick:1511995288200548373>",
    "warn":        "<:warn:1511995398393299014>",
    "mute":        "<:mute:1511995732775796827>",
    "unmute":      "<:unmute:1511995732775796827>",
    "welcome":     "<:welcome:1511996084241698957>",
    "new_account": "<:new_account:1511995398393299014>",
    "check":       "<:check:1511996429630181458>",
    "cross":       "<:cross:1512824131232465076>",
    "crown":       "<:crown:1511996955713081344>",
    "ticket":      "<:ticket:1511997222760349797>",
    "giveaway":    "<:giveaway:1511997374090969186>",
    "info":        "<:info:1511997506215477390>",
    "ping":        "<:ping:1511997619042517013>",
    "loading":     "<:loading6667:1512806374395084940>",
    "error":       "<:cross:1512824131232465076>",
}

# Logging Channels
LOG_CHANNELS = {
    "command_errors": 1486822134109180049,
}

# Role Menus 
COLOR_ROLES = {
    "soft grass":    1486806728854077512,
    "chocolate":     1486810125330022490,
    "roze":          1486810495229890851,
    "gumball":       1486806282219421726,
    "violette":      1486806667982012426,
    "black panther": 1486806539716132976,
    "clear sky":     1486806394572116121,
    "sand stone":    1486806610776162364,
    "crimson moon":  1486806247662551232,
    "goldie":        1498744933513957426,
}
GAME_ROLES = {
    "minecraft":     1487197718538485760,
    "pubg":          1487197752843567184,
    "valorant":      1487197792001724469,
    "combat master": 1487197844090781706,
    "among us":      1487197878920151232,
    "roblox":        1487198366528966798,
    "rocket league": 1487197958813384848,
    "code names":    1487198514726305903,
    "gta":           1487198455053942854,
}
OTHER_ROLES = {"game mentions": 1503766099102273556}

SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
EMBED_COLOR = 0x393a41