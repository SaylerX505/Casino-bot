"""Artwork generation and optional image downloads for Russian roulette."""

import io
import logging
import os

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)


async def fetch_avatar(session, user, size=(80, 80)):
    try:
        async with session.get(user.display_avatar.url) as response:
            data = await response.read()
            return Image.open(io.BytesIO(data)).convert("RGBA").resize(size)
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
        logger.debug("Could not load a player avatar for the roulette image", exc_info=True)
        return Image.new("RGBA", size, (50, 50, 50, 255))


async def generate_sniper_image(player):
    width, height = 800, 500
    base = Image.new("RGBA", (width, height), (15, 15, 15, 255))
    draw = ImageDraw.Draw(base)

    center_x, center_y = width // 2, height // 2
    radius = 180
    logo_path = os.path.join(os.getcwd(), "server_logo.png")

    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((int(radius * 1.5), int(radius * 1.5)))
            logo = logo.filter(ImageFilter.GaussianBlur(5))
            base.paste(
                logo,
                (center_x - logo.width // 2, center_y - logo.height // 2),
                logo,
            )
        except (OSError, ValueError):
            logger.debug("Optional roulette scene logo could not be loaded", exc_info=True)

    draw.ellipse(
        (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
        outline=(255, 50, 50, 200),
        width=4,
    )
    draw.line(
        (center_x, center_y - radius, center_x, center_y + radius),
        fill=(255, 50, 50, 150),
        width=2,
    )
    draw.line(
        (center_x - radius, center_y, center_x + radius, center_y),
        fill=(255, 50, 50, 150),
        width=2,
    )
    draw.ellipse(
        (center_x - 4, center_y - 4, center_x + 4, center_y + 4),
        fill=(255, 0, 0, 255),
    )

    try:
        async with aiohttp.ClientSession() as session:
            avatar = await fetch_avatar(session, player, (80, 80))
        base.paste(avatar, (40, height - 120), avatar)
        draw.rectangle(
            (40, height - 120, 120, height - 40),
            outline=(0, 255, 0, 200),
            width=3,
        )
        try:
            font = ImageFont.truetype("tahoma.ttf", 20)
        except OSError:
            logger.debug("Roulette image font unavailable; using the default font", exc_info=True)
            font = ImageFont.load_default()
        draw.text(
            (40, height - 35),
            "SNIPER ACTIVE",
            fill=(0, 255, 0, 200),
            font=font,
        )
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
        logger.debug("Could not compose the optional roulette avatar overlay", exc_info=True)

    output = io.BytesIO()
    base.save(output, format="PNG")
    output.seek(0)
    return output
