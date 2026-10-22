"""Artwork generation and optional image downloads for Russian roulette."""

import asyncio
import io
import logging
import os

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)


async def fetch_avatar(session, user, size=(80, 80)):
    try:
        async with session.get(user.display_avatar.url) as resp:
            data = await resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGBA").resize(size)
            return img
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        logger.debug("Could not load a player avatar for the roulette image", exc_info=True)
        return Image.new("RGBA", size, (50, 50, 50, 255))

async def generate_sniper_image(player):
    width, height = 800, 500
    # --- SCENE BACKGROUND ---
    base = Image.new('RGBA', (width, height), (15, 15, 15, 255))
    draw = ImageDraw.Draw(base)
    
    cx, cy = width//2, height//2
    # --- SCOPE GEOMETRY ---
    radius = 180
    
    # --- SCOPE LOGO ---
    logo_path = os.path.join(os.getcwd(), "server_logo.png")
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            # --- LOGO SCALING ---
            logo = logo.resize((int(radius * 1.5), int(radius * 1.5)))
            logo = logo.filter(ImageFilter.GaussianBlur(5))
            # --- LOGO POSITIONING ---
            base.paste(logo, (cx - logo.width//2, cy - logo.height//2), logo)
        except (OSError, ValueError):
            logger.debug("Optional roulette scene logo could not be loaded", exc_info=True)
            
    # --- SCOPE DETAILS ---
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=(255, 50, 50, 200), width=4)
    draw.line((cx, cy-radius, cx, cy+radius), fill=(255, 50, 50, 150), width=2)
    draw.line((cx-radius, cy, cx+radius, cy), fill=(255, 50, 50, 150), width=2)
    draw.ellipse((cx-4, cy-4, cx+4, cy+4), fill=(255, 0, 0, 255))
    
    # --- HUNTER AVATAR ---
    try:
        async with aiohttp.ClientSession() as session:
            avatar = await fetch_avatar(session, player, (80, 80))
        base.paste(avatar, (40, height - 120), avatar)
        draw.rectangle((40, height - 120, 120, height - 40), outline=(0, 255, 0, 200), width=3)

        try:
            font = ImageFont.truetype("tahoma.ttf", 20)
        except OSError:
            logger.debug("Roulette image font unavailable; using the default font", exc_info=True)
            font = ImageFont.load_default()
        draw.text((40, height - 35), "SNIPER ACTIVE", fill=(0, 255, 0, 200), font=font)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        logger.debug("Could not compose the optional roulette avatar overlay", exc_info=True)
            
    out_bytes = io.BytesIO()
    base.save(out_bytes, format='PNG')
    out_bytes.seek(0)
    return out_bytes
