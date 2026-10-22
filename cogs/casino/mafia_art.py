"""Image-generation helpers for Mafia; no Discord game lifecycle or UI logic."""

import asyncio
import io
import logging

import aiohttp
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


AVATAR_SETTINGS = {
    "dead_civilian": {"pos": (655, 425), "size": (50, 50)},
    "dead_doctor":   {"pos": (189, 331), "size": (90, 90)},
    "saved_person":  {"pos": (190, 144), "size": (130, 130)},
    "mafia_1": [{"pos": (520, 331), "size": (180, 180)}], 
    "mafia_2": [{"pos": (250, 285), "size": (150, 150)}, {"pos": (565, 353), "size": (150, 150)}],
    "mafia_3": [{"pos": (133, 147), "size": (50, 50)}, {"pos": (316, 180), "size": (45, 45)}, {"pos": (512, 171), "size": (50, 50)}]
}

async def fetch_avatar(session, user, size):
    try:
        async with session.get(user.display_avatar.url) as resp:
            data = await resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGBA").resize(size)
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size[0], size[1]), fill=255)
            circular = Image.new("RGBA", size, (0, 0, 0, 0))
            circular.paste(img, (0, 0), mask)
            return circular
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Could not load a player avatar for Mafia art", exc_info=True)
        return Image.new("RGBA", size, (50, 50, 50, 255))

async def generate_billboard(alive_players):
    try:
        base = Image.open("mafiagame/billboard.jpg").convert("RGBA")
    except Exception:
        logger.debug("Mafia billboard asset unavailable; using a fallback canvas", exc_info=True)
        base = Image.new('RGBA', (1200, 600), (30, 30, 30, 255))
    width = base.width
    cols, avatar_size, padding_x, padding_y, start_y = 9, 70, 25, 30, 100
    rows = [alive_players[i:i + cols] for i in range(0, len(alive_players), cols)]
    async with aiohttp.ClientSession() as session:
        for row_idx, row_players in enumerate(rows):
            items_in_row = len(row_players)
            row_width = (items_in_row * avatar_size) + ((items_in_row - 1) * padding_x)
            row_start_x = (width - row_width) // 2
            for col_idx, player in enumerate(row_players):
                x = row_start_x + (col_idx * (avatar_size + padding_x))
                y = start_y + (row_idx * (avatar_size + padding_y))
                avatar = await fetch_avatar(session, player, size=(avatar_size, avatar_size))
                base.paste(avatar, (int(x), int(y)), avatar)
    out = io.BytesIO()
    base.save(out, format='PNG')
    out.seek(0)
    return out

async def generate_action_image(base_img, target, key):
    try:
        base = Image.open(f"mafiagame/{base_img}").convert("RGBA")
    except Exception:
        logger.debug("Mafia action asset unavailable; using a fallback canvas", exc_info=True)
        base = Image.new('RGBA', (1200, 600), (0, 0, 0, 255))
    s = AVATAR_SETTINGS[key]
    async with aiohttp.ClientSession() as session:
        avatar = await fetch_avatar(session, target, size=s["size"])
        base.paste(avatar, (int(s["pos"][0]-s["size"][0]/2), int(s["pos"][1]-s["size"][1]/2)), avatar)
    out = io.BytesIO()
    base.save(out, format='PNG')
    out.seek(0)
    return out

async def generate_mafia_reveal(mafias):
    count = len(mafias)
    try:
        base = Image.open(f"mafiagame/mafia_{count}.jpg").convert("RGBA")
    except Exception:
        logger.debug("Mafia reveal asset unavailable; using a fallback canvas", exc_info=True)
        base = Image.new('RGBA', (1200, 600), (20, 0, 0, 255))
    configs = AVATAR_SETTINGS[f"mafia_{count}"]
    async with aiohttp.ClientSession() as session:
        for i, p in enumerate(mafias):
            if i >= len(configs): break
            pos, size = configs[i]["pos"], configs[i]["size"]
            avatar = await fetch_avatar(session, p, size=size)
            base.paste(avatar, (int(pos[0]-size[0]/2), int(pos[1]-size[1]/2)), avatar)
    out = io.BytesIO()
    base.save(out, format='PNG')
    out.seek(0)
    return out
