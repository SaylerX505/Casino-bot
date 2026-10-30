"""Shared solo betting games with durable economy settlement and Components V2 UI."""
# ruff: noqa: I001

import asyncio
import io
import logging
import math
import secrets
import uuid
from collections.abc import Callable
from functools import partial

import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.currency_catalog import get_coin_emoji
from cogs.casino.bet_validation import parse_bet_amount
from cogs.utility.diagnostics import DiagnosticViewMixin
from cogs.utility.theme import THEME, StatusPanel

logger = logging.getLogger(__name__)

MIN_BET = 10
MAX_BET = 5_000
GROSS_WIN_MULTIPLIER_NUMERATOR = 190
GROSS_WIN_MULTIPLIER_DENOMINATOR = 100
SIDES = {
    "heads": {"label": "Heads", "emoji": "🪙"},
    "tails": {"label": "Tails", "emoji": "🪙"},
}
DICE_CHOICES = {str(number): {"label": str(number), "emoji": None} for number in range(1, 7)}
SLOT_SYMBOLS = ("🍒", "🍋", "🍊", "🍇", "⭐", "💎")
SLOT_TRIPLE_PAYOUTS = {"🍒": 500, "🍋": 600, "🍊": 700, "🍇": 800, "⭐": 1_000, "💎": 1_500}
SLOT_CHOICES = {"spin": {"label": "Spin", "emoji": "🎰"}}
_SOLO_GAME_ACCENTS = {
    "coinflip": THEME["gold"],
    "dice": THEME["teal"],
    "slots": THEME["red_dark"],
}
_RECOVERY_DELAYS_SECONDS = (2, 5, 15, 30, 60)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_SESSIONS: dict[int, discord.ui.View] = {}


def _art_font(size: int, *, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def _draw_die(draw, center_x: int, center_y: int, size: int, value: int) -> None:
    left = center_x - size // 2
    top = center_y - size // 2
    right = left + size
    bottom = top + size
    draw.rounded_rectangle(
        (left + 8, top + 12, right + 8, bottom + 12),
        radius=max(14, size // 7),
        fill=(5, 9, 16),
    )
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=max(14, size // 7),
        fill=(246, 245, 239),
        outline=(255, 255, 250),
        width=4,
    )
    draw.rounded_rectangle(
        (left + 9, top + 9, right - 9, bottom - 9),
        radius=max(10, size // 9),
        outline=(213, 218, 220),
        width=2,
    )
    positions = {
        1: ((1, 1),),
        2: ((0, 0), (2, 2)),
        3: ((0, 0), (1, 1), (2, 2)),
        4: ((0, 0), (2, 0), (0, 2), (2, 2)),
        5: ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
        6: ((0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2)),
    }.get(max(1, min(6, int(value))), ())
    pip_radius = max(5, size // 15)
    for col, row in positions:
        x = left + size * (col + 1) // 4
        y = top + size * (row + 1) // 4
        draw.ellipse(
            (x - pip_radius, y - pip_radius, x + pip_radius, y + pip_radius),
            fill=(29, 38, 51),
        )


def _draw_slot_symbol(draw, symbol: str, center_x: int, center_y: int, size: int) -> None:
    scale = size / 100
    def p(value):
        return int(value * scale)
    x, y = center_x, center_y
    if symbol == "🍒":
        draw.line((x - p(8), y - p(6), x - p(17), y - p(35)), fill=(48, 136, 91), width=p(5))
        draw.line((x + p(8), y - p(6), x + p(18), y - p(38)), fill=(48, 136, 91), width=p(5))
        draw.ellipse((x - p(39), y - p(9), x - p(1), y + p(30)), fill=(192, 34, 59))
        draw.ellipse((x + p(1), y - p(12), x + p(39), y + p(26)), fill=(223, 44, 65))
        draw.ellipse((x - p(29), y - p(1), x - p(19), y + p(9)), fill=(255, 155, 153))
        draw.ellipse((x + p(11), y - p(4), x + p(21), y + p(6)), fill=(255, 160, 160))
        draw.ellipse((x - p(6), y - p(40), x + p(25), y - p(25)), fill=(57, 162, 89))
    elif symbol == "🍋":
        draw.ellipse((x - p(42), y - p(25), x + p(42), y + p(25)), fill=(245, 199, 44))
        draw.ellipse((x - p(31), y - p(17), x + p(24), y + p(12)), fill=(255, 225, 92))
        draw.polygon(
            [(x - p(41), y), (x - p(51), y - p(5)), (x - p(40), y - p(13))],
            fill=(255, 222, 92),
        )
        draw.polygon(
            [(x + p(41), y), (x + p(51), y - p(5)), (x + p(40), y - p(13))],
            fill=(255, 222, 92),
        )
        draw.ellipse((x + p(4), y - p(34), x + p(32), y - p(22)), fill=(66, 165, 96))
    elif symbol == "🍊":
        draw.ellipse((x - p(34), y - p(34), x + p(34), y + p(34)), fill=(244, 126, 36))
        draw.ellipse((x - p(22), y - p(24), x + p(5), y + p(2)), fill=(255, 169, 74))
        draw.ellipse((x - p(8), y - p(43), x + p(9), y - p(30)), fill=(72, 161, 80))
    elif symbol == "🍇":
        grapes = [
            (-20, -12), (0, -18), (20, -12), (-29, 7), (-9, 6), (11, 7),
            (-18, 25), (2, 25), (-7, 42),
        ]
        for dx, dy in grapes:
            radius = p(13)
            draw.ellipse(
                (x + p(dx) - radius, y + p(dy) - radius,
                 x + p(dx) + radius, y + p(dy) + radius),
                fill=(116, 75, 190),
                outline=(175, 145, 231),
                width=max(1, p(2)),
            )
        draw.polygon(
            [(x - p(4), y - p(28)), (x + p(7), y - p(47)),
             (x + p(28), y - p(39)), (x + p(11), y - p(24))],
            fill=(57, 159, 87),
        )
    elif symbol == "⭐":
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            radius = p(43 if i % 2 == 0 else 19)
            points.append((x + int(math.cos(angle) * radius), y + int(math.sin(angle) * radius)))
        draw.polygon(points, fill=(246, 196, 61), outline=(255, 232, 145))
    else:
        points = [
            (x, y - p(46)), (x + p(34), y - p(6)), (x, y + p(45)),
            (x - p(34), y - p(6)),
        ]
        draw.polygon(points, fill=(65, 198, 218), outline=(180, 249, 255))
        draw.line((x, y - p(46), x, y + p(45)), fill=(184, 252, 255), width=max(1, p(2)))
        draw.line((x - p(34), y - p(6), x + p(34), y - p(6)), fill=(184, 252, 255), width=max(1, p(2)))


def _render_solo_game_art(
    game_type: str,
    *,
    choice: str | None = None,
    result: str | None = None,
    won: bool | None = None,
) -> bytes:
    """Draw polished, result-aware visuals for coinflip, dice, and slots V2 panels."""
    width, height = 1200, 420
    is_slots = game_type == "slots"
    is_coinflip = game_type == "coinflip"
    top = (44, 16, 33) if is_slots else (45, 30, 12) if is_coinflip else (10, 30, 43)
    bottom = (12, 14, 24) if is_slots else (15, 13, 10) if is_coinflip else (7, 14, 24)
    accent = (231, 173, 54) if is_slots or is_coinflip else (56, 197, 198)
    image = Image.new("RGB", (width, height), bottom)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    for offset in range(-height, width, 92):
        draw.line((offset, 0, offset + height, height), fill=(*accent,), width=1)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=30, outline=accent, width=3)
    draw.ellipse((830, -220, 1300, 245), fill=top)
    draw.ellipse((-160, 220, 300, 650), fill=top)

    title = "SLOT MACHINE" if is_slots else "COIN FLIP" if is_coinflip else "DICE TABLE"
    draw.text((68, 48), "ÇUKUR  /  CASINO", font=_art_font(22, bold=True), fill=accent)
    title_size = 43
    title_font = _art_font(title_size, bold=True)
    # Keep the full SLOT MACHINE label left of the cabinet instead of letting
    # its final letter sit underneath the machine artwork.
    if is_slots:
        while draw.textbbox((0, 0), title, font=title_font)[2] > 300 and title_size > 28:
            title_size -= 1
            title_font = _art_font(title_size, bold=True)
    draw.text((68, 82), title, font=title_font, fill=(245, 245, 239))
    draw.line((68, 145, 355, 145), fill=accent, width=4)

    if is_slots:
        symbols = result.split(" · ") if result else ["🍒", "🍋", "⭐"]
        symbols = (symbols + ["🍒", "🍋", "⭐"])[:3]
        draw.rounded_rectangle((410, 42, 875, 375), radius=28, fill=(34, 20, 31), outline=accent, width=5)
        draw.rounded_rectangle((432, 62, 853, 350), radius=18, fill=(9, 11, 18), outline=(110, 76, 50), width=2)
        for index, symbol in enumerate(symbols):
            x1 = 448 + index * 135
            draw.rounded_rectangle(
                (x1, 82, x1 + 119, 319), radius=17,
                fill=(236, 236, 222), outline=(255, 255, 244), width=3,
            )
            draw.rounded_rectangle(
                (x1 + 7, 89, x1 + 112, 311), radius=12,
                outline=(199, 201, 190), width=2,
            )
            _draw_slot_symbol(draw, symbol, x1 + 59, 202, 80)
        draw.rounded_rectangle((895, 93, 1127, 326), radius=24, fill=(20, 23, 30), outline=accent, width=3)
        draw.ellipse((965, 121, 1057, 213), fill=(72, 21, 34), outline=(246, 204, 111), width=5)
        draw.ellipse((991, 147, 1031, 187), fill=(233, 179, 54), outline=(255, 227, 146), width=3)
        if won is not None:
            badge_color = (48, 166, 112) if won else (170, 48, 58)
            label = "WIN" if won else "NO WIN"
            draw.rounded_rectangle((915, 244, 1107, 292), radius=16, fill=badge_color)
            draw.text((1011, 268), label, font=_art_font(20, bold=True), fill=(255, 255, 248), anchor="mm")
        else:
            draw.text((1011, 268), "SPIN", font=_art_font(22, bold=True), fill=(245, 245, 239), anchor="mm")
    elif is_coinflip:
        draw.rounded_rectangle((430, 40, 840, 380), radius=28, fill=(25, 22, 18), outline=accent, width=4)
        draw.ellipse((478, 55, 792, 369), fill=(118, 76, 24), outline=(255, 225, 147), width=8)
        draw.ellipse((493, 70, 777, 354), fill=(227, 176, 64), outline=(255, 239, 181), width=4)
        draw.ellipse((510, 87, 760, 337), fill=(31, 29, 27), outline=(172, 126, 47), width=4)
        face = "HEADS" if result == "heads" else "TAILS" if result == "tails" else "CKR"
        draw.text((635, 185), "ÇUKUR", font=_art_font(19, bold=True), fill=(224, 179, 77), anchor="mm")
        draw.line((555, 208, 715, 208), fill=(172, 126, 47), width=3)
        draw.text((635, 253), face, font=_art_font(31, bold=True), fill=(255, 229, 157), anchor="mm")
        draw.rounded_rectangle((864, 54, 1132, 366), radius=24, fill=(20, 21, 24), outline=accent, width=3)
        draw.text((998, 91), "YOUR PICK", font=_art_font(16, bold=True), fill=(195, 177, 137), anchor="mm")
        picked = "HEADS" if choice == "heads" else "TAILS" if choice == "tails" else "—"
        draw.text((998, 129), picked, font=_art_font(25, bold=True), fill=(248, 242, 224), anchor="mm")
        draw.line((890, 158, 1106, 158), fill=(90, 77, 53), width=2)
        draw.text((998, 190), "RESULT", font=_art_font(16, bold=True), fill=(195, 177, 137), anchor="mm")
        result_label = "HEADS" if result == "heads" else "TAILS" if result == "tails" else "READY"
        draw.text((998, 228), result_label, font=_art_font(25, bold=True), fill=(248, 242, 224), anchor="mm")
        if won is not None:
            badge_color = (48, 166, 112) if won else (170, 48, 58)
            badge_label = "WIN" if won else "NO WIN"
        else:
            badge_color, badge_label = (74, 68, 51), "FLIP"
        draw.rounded_rectangle((900, 270, 1096, 318), radius=15, fill=badge_color)
        draw.text((998, 294), badge_label, font=_art_font(20, bold=True), fill=(255, 255, 248), anchor="mm")
    else:
        if choice is not None and result is not None:
            left_value = max(1, min(6, int(choice)))
            right_value = max(1, min(6, int(result)))
            _draw_die(draw, 590, 225, 176, left_value)
            _draw_die(draw, 820, 225, 176, right_value)
            draw.text((590, 105), "YOUR PICK", font=_art_font(18, bold=True), fill=accent, anchor="mm")
            draw.text((820, 105), "ROLLED", font=_art_font(18, bold=True), fill=accent, anchor="mm")
            draw.text((704, 222), "›", font=_art_font(64, bold=True), fill=(244, 244, 237), anchor="mm")
            if won is not None:
                badge_color = (48, 166, 112) if won else (170, 48, 58)
                label = "WIN" if won else "NO WIN"
                draw.rounded_rectangle((918, 178, 1124, 250), radius=21, fill=badge_color)
                draw.text((1021, 214), label, font=_art_font(24, bold=True), fill=(255, 255, 248), anchor="mm")
        else:
            _draw_die(draw, 560, 244, 166, 2)
            _draw_die(draw, 765, 202, 190, 5)
            _draw_die(draw, 966, 250, 146, 3)
            draw.ellipse((493, 157, 627, 291), outline=accent, width=2)
            draw.ellipse((694, 107, 836, 249), outline=accent, width=2)
            draw.ellipse((910, 192, 1022, 304), outline=accent, width=2)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()



def _render_advanced_instant_art(
    game_type: str,
    *,
    choice: str | None = None,
    result: str | None = None,
    won: bool | None = None,
    path_progress: int = 8,
    spin_offset: float = 0.0,
    rolling: bool = False,
) -> bytes:
    """Render bespoke Plinko and number-roulette artwork for Components V2 panels."""
    width, height = 1200, 420
    is_plinko = game_type == "plinko"
    top = (37, 19, 43) if is_plinko else (17, 26, 39)
    bottom = (9, 12, 22)
    accent = (244, 187, 83) if is_plinko else (79, 204, 176)
    image = Image.new("RGB", (width, height), bottom)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    for offset in range(-height, width, 92):
        draw.line((offset, 0, offset + height, height), fill=(*accent,), width=1)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=30, outline=accent, width=3)
    draw.ellipse((830, -220, 1300, 245), fill=top)
    draw.ellipse((-160, 220, 300, 650), fill=top)
    draw.text((68, 48), "ÇUKUR  /  CASINO", font=_art_font(22, bold=True), fill=accent)
    title = "PLINKO" if is_plinko else "NUMBER ROULETTE"
    title_size = 43
    title_font = _art_font(title_size, bold=True)
    while draw.textbbox((0, 0), title, font=title_font)[2] > 300 and title_size > 28:
        title_size -= 1
        title_font = _art_font(title_size, bold=True)
    draw.text((68, 82), title, font=title_font, fill=(247, 245, 239))
    draw.line((68, 145, 355, 145), fill=accent, width=4)

    if is_plinko:
        multipliers = (3.0, 2.0, 1.5, 1.1, 0.0, 1.1, 1.5, 2.0, 3.0)
        slot = None
        path = None
        if result and "slot=" in result:
            try:
                parts = dict(part.split("=", 1) for part in result.split(";") if "=" in part)
                slot = max(0, min(8, int(parts["slot"])))
                path = parts.get("path")
                if path is not None and (len(path) != 8 or any(step not in "LR" for step in path)):
                    path = None
            except (ValueError, KeyError):
                slot = None
                path = None

        draw.rounded_rectangle((390, 38, 901, 380), radius=24, fill=(13, 17, 27), outline=accent, width=4)
        center_x, top_y = 649, 71
        path_points = [(center_x, top_y)]
        right_steps = 0
        progress = max(0, min(8, int(path_progress))) if path is not None else 0
        for row in range(8):
            y = top_y + row * 27
            for col in range(row + 1):
                x = center_x + (2 * col - row) * 27
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(226, 226, 225), outline=accent, width=1)
            if path is not None and row < progress:
                right_steps += path[row] == "R"
                path_points.append((int(center_x + (2 * right_steps - row - 1) * 27), top_y + (row + 1) * 27))
        if slot is not None and path is not None:
            if progress >= 8:
                path_points.append((433 + slot * 54, 302))
            draw.line(path_points, fill=(255, 216, 111), width=4)
            ball_x, ball_y = path_points[-1]
            draw.ellipse((ball_x - 9, ball_y - 9, ball_x + 9, ball_y + 9), fill=(255, 221, 121), outline=(255, 249, 218), width=3)
        else:
            draw.ellipse((center_x - 9, top_y - 16, center_x + 9, top_y + 2), fill=(255, 221, 121), outline=(255, 249, 218), width=3)

        for index, multiplier in enumerate(multipliers):
            left = 409 + index * 54
            active = index == slot
            fill = (95, 62, 27) if active else (25, 32, 43)
            draw.rounded_rectangle((left, 307, left + 49, 355), radius=8, fill=fill, outline=accent, width=2)
            label = f"{multiplier:g}x"
            draw.text((left + 24, 331), label, font=_art_font(15, bold=True), fill=(255, 245, 222), anchor="mm")
        draw.text((645, 365), "DROP  /  LAND  /  WIN", font=_art_font(14, bold=True), fill=accent, anchor="mm")
        draw.text((73, 191), "ONE DROP.", font=_art_font(20, bold=True), fill=(237, 233, 224))
        draw.text((73, 226), "NINE BINS.", font=_art_font(20, bold=True), fill=(237, 233, 224))
        draw.text((73, 261), "ONE RESULT.", font=_art_font(20, bold=True), fill=(237, 233, 224))
        if won is not None:
            badge = (48, 166, 112) if won else (170, 48, 58)
            draw.rounded_rectangle((61, 306, 308, 357), radius=15, fill=badge)
            draw.text((184, 331), "WIN" if won else "NO WIN", font=_art_font(22, bold=True), fill=(255, 255, 248), anchor="mm")
    else:
        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        center_x, center_y, radius = 696, 214, 161
        draw.ellipse((center_x - radius - 8, center_y - radius - 8, center_x + radius + 8, center_y + radius + 8), fill=(24, 36, 33), outline=accent, width=4)
        for number in range(37):
            start = -90 + number * (360 / 37) + spin_offset
            end = -90 + (number + 1) * (360 / 37) + spin_offset
            fill = (35, 133, 87) if number == 0 else (168, 43, 52) if number in red_numbers else (21, 25, 32)
            draw.pieslice((center_x - radius, center_y - radius, center_x + radius, center_y + radius), start=start, end=end, fill=fill, outline=(227, 224, 210), width=1)
            angle = math.radians((start + end) / 2)
            label_x = center_x + int(math.cos(angle) * 132)
            label_y = center_y + int(math.sin(angle) * 132)
            draw.text((label_x, label_y), str(number), font=_art_font(10, bold=True), fill=(255, 250, 237), anchor="mm")
        draw.ellipse((center_x - 77, center_y - 77, center_x + 77, center_y + 77), fill=(11, 18, 23), outline=accent, width=4)
        number_text = "?" if rolling else result if result is not None and str(result).isdigit() else "?"
        result_fill = (72, 183, 121) if number_text == "0" else (244, 238, 220)
        draw.text((center_x, center_y - 5), number_text, font=_art_font(57, bold=True), fill=result_fill, anchor="mm")
        draw.text((center_x, center_y + 46), "RESULT", font=_art_font(13, bold=True), fill=accent, anchor="mm")

        draw.rounded_rectangle((920, 72, 1140, 354), radius=22, fill=(18, 24, 32), outline=accent, width=3)
        draw.text((1030, 112), "YOUR NUMBER", font=_art_font(15, bold=True), fill=(177, 192, 193), anchor="mm")
        pick_text = str(choice) if choice is not None and str(choice).isdigit() else "—"
        draw.text((1030, 164), pick_text, font=_art_font(43, bold=True), fill=(248, 244, 233), anchor="mm")
        draw.line((946, 199, 1114, 199), fill=(73, 93, 92), width=2)
        if won is not None:
            badge = (48, 166, 112) if won else (170, 48, 58)
            draw.rounded_rectangle((944, 231, 1116, 287), radius=15, fill=badge)
            draw.text((1030, 259), "WIN" if won else "NO WIN", font=_art_font(21, bold=True), fill=(255, 255, 248), anchor="mm")
        else:
            draw.text((1030, 258), "0 — 36", font=_art_font(19, bold=True), fill=accent, anchor="mm")
        draw.text((74, 204), "EUROPEAN", font=_art_font(18, bold=True), fill=(237, 233, 224))
        draw.text((74, 235), "SINGLE ZERO", font=_art_font(18, bold=True), fill=(237, 233, 224))
        draw.text((74, 266), "37 NUMBERS", font=_art_font(18, bold=True), fill=(237, 233, 224))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()

def has_active_solo_session(user_id: int) -> bool:
    """Whether this member already has an open solo-game session."""
    current = _ACTIVE_SESSIONS.get(int(user_id))
    return current is not None and not getattr(current, "done", False) and not current.is_finished()


def claim_solo_session(user_id: int, view: discord.ui.View) -> bool:
    """Atomically reserve the member's one active solo-game session."""
    user_id = int(user_id)
    if has_active_solo_session(user_id):
        return False
    _ACTIVE_SESSIONS[user_id] = view
    return True


def release_solo_session(user_id: int, view: discord.ui.View) -> None:
    """Release a reserved session without clearing a newer session."""
    if _ACTIVE_SESSIONS.get(int(user_id)) is view:
        _ACTIVE_SESSIONS.pop(int(user_id), None)



def _gross_payout(
    stake: int,
    numerator: int = GROSS_WIN_MULTIPLIER_NUMERATOR,
    denominator: int = GROSS_WIN_MULTIPLIER_DENOMINATOR,
) -> int:
    """Return the advertised gross payout for a virtual-currency wager."""
    if stake <= 0 or numerator <= 0 or denominator <= 0:
        raise ValueError("stake and payout multiplier must be positive")
    return (stake * numerator) // denominator


def _spin_slots(_choice: str, stake: int) -> tuple[str, bool, int]:
    """Return a three-reel slot result, win flag, and gross payout in integer coins."""
    if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
        raise ValueError("slot stake must be a positive integer")
    symbols = tuple(secrets.choice(SLOT_SYMBOLS) for _ in range(3))
    result = " · ".join(symbols)
    if symbols[0] == symbols[1] == symbols[2]:
        return result, True, (stake * SLOT_TRIPLE_PAYOUTS[symbols[0]]) // 100
    if len(set(symbols)) == 2:
        return result, True, (stake * 170) // 100
    return result, False, 0


_STARTUP_PANEL_TASKS: set[asyncio.Task] = set()
_GAME_TITLES = {
    "coinflip": "COIN FLIP",
    "dice": "DICE",
    "slots": "SLOTS",
    "blackjack": "Blackjack",
    "mines": "Mines",
    "crash": "Crash",
    "plinko": "Plinko",
    "number_roulette": "NUMBER ROULETTE",
    "hilo": "Hi-Lo",
}


async def _publish_interrupted_wager_panel(bot, record, receipt):
    """Replace a stale game panel after the matching wager has been safely refunded."""
    if (
        receipt.get("status") != "refunded"
        or receipt.get("outcome") != "refund"
    ):
        return
    channel_id = record.get("channel_id")
    message_id = record.get("message_id")
    if not channel_id or not message_id:
        return

    try:
        await bot.wait_until_ready()
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        title = _GAME_TITLES.get(str(record.get("game_type")), "GAME")
        stake = int(record.get("stake", receipt.get("stake", 0)))
        coin_emoji = get_coin_emoji(getattr(channel, "guild", None))
        await message.edit(
            view=StatusPanel(
                "ROUND REFUNDED",
                (
                    f"Your **{title}** round was interrupted. "
                    f"Bet returned: **{stake:,} {coin_emoji}**. No result was recorded."
                ),
                "warning",
            )
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logger.info(
            "Interrupted wager refunded but original panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logger.warning(
            "Interrupted wager refunded but the bot cannot access its panel game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logger.warning(
            "Interrupted wager refunded but its old panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )
    except Exception:
        logger.exception(
            "Interrupted wager was refunded, but an unexpected error blocked panel cleanup game_id=%s",
            record.get("game_id"),
        )


async def recover_orphaned_casino_wagers(bot, schedule_recovery):
    """Refund unfinished solo bets at startup and close their stale Discord panels."""
    records = await asyncio.to_thread(bank.active_casino_wager_records)
    refunded = await asyncio.to_thread(bank.refund_orphaned_casino_wagers)
    active_ids = await asyncio.to_thread(bank.active_casino_wager_ids)
    active_set = set(active_ids)
    records_by_id = {str(record["game_id"]): record for record in records}

    for game_id, record in records_by_id.items():
        has_panel = bool(record.get("channel_id") and record.get("message_id"))
        if game_id in active_set:
            callback = (
                partial(_publish_interrupted_wager_panel, bot, record)
                if has_panel
                else None
            )
            schedule_recovery(game_id, force_refund=True, callback=callback)
            continue

        receipt = await asyncio.to_thread(bank.casino_wager_status, game_id)
        if (
            has_panel
            and receipt is not None
            and receipt.get("status") == "refunded"
            and receipt.get("outcome") == "refund"
        ):
            task = asyncio.create_task(
                _publish_interrupted_wager_panel(bot, record, receipt)
            )
            _STARTUP_PANEL_TASKS.add(task)
            task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

    for game_id in active_ids:
        if str(game_id) not in records_by_id:
            schedule_recovery(game_id, force_refund=True)

    return refunded, active_ids


class SoloWagerView(DiagnosticViewMixin, LayoutView):
    """Requester-bound solo game with a distinct final receipt and durable settlement."""

    def __init__(
        self,
        cog,
        requester,
        amount: int,
        *,
        game_type: str = "coinflip",
        game_title: str = "COIN FLIP",
        choices: dict | None = None,
        payout_numerator: int = GROSS_WIN_MULTIPLIER_NUMERATOR,
        payout_denominator: int = GROSS_WIN_MULTIPLIER_DENOMINATOR,
        odds_description: str = "50%",
        instructions: str = "Choose heads or tails.",
        payout_description: str | None = None,
        outcome_resolver: Callable[[str, int], tuple[str, bool, int]] | None = None,
        context=None,
    ):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester = requester
        self.coin_emoji = get_coin_emoji(getattr(requester, "guild", None))
        self.context = context
        self.amount = amount
        self.result_published = False
        self.game_type = game_type
        self.game_title = game_title
        self.choices = choices or SIDES
        self.payout_numerator = payout_numerator
        self.payout_denominator = payout_denominator
        self.odds_description = odds_description
        self.instructions = instructions
        self.payout_description = payout_description
        self.outcome_resolver = outcome_resolver
        self.message = None
        self.game_id = None
        self.done = False
        self.resolving = False
        art_types = {"coinflip", "dice", "slots", "plinko", "number_roulette"}
        self._art_filename = f"{game_type}.png" if game_type in art_types else None
        if game_type in {"plinko", "number_roulette"}:
            self._art_bytes = _render_advanced_instant_art(game_type)
        else:
            self._art_bytes = _render_solo_game_art(game_type) if self._art_filename is not None else None
        self._buttons = {}
        self.selected_number = None
        self._confirm_button = None
        self._replay_button = None
        self._panel_text = TextDisplay(self._waiting_content())

        choice_buttons = []
        number_selects = []
        for choice, metadata in self.choices.items():
            if self.game_type == "number_roulette":
                continue
            button = discord.ui.Button(
                label=metadata["label"],
                style=discord.ButtonStyle.secondary,
                custom_id=f"{self.game_type}_{choice}",
            )
            button.callback = partial(self.choose, side=choice)
            self._buttons[choice] = button
            choice_buttons.append(button)

        if self.game_type == "number_roulette":
            for lower, upper, placeholder in (
                (0, 18, "Choose 0–18"),
                (19, 36, "Choose 19–36"),
            ):
                options = [
                    discord.SelectOption(
                        label=self.choices[str(number)]["label"],
                        value=str(number),
                    )
                    for number in range(lower, upper + 1)
                ]
                selector = discord.ui.Select(
                    placeholder=placeholder,
                    min_values=1,
                    max_values=1,
                    options=options,
                    custom_id=f"number_roulette_{lower}_{upper}",
                )
                async def choose_number(interaction, current_selector=selector):
                    self.selected_number = current_selector.values[0]
                    self._confirm_button.disabled = False
                    self._panel_text.content = self._waiting_content()
                    await interaction.response.edit_message(view=self)
                selector.callback = choose_number
                self._buttons[f"numbers_{lower}_{upper}"] = selector
                number_selects.append(selector)

            self._confirm_button = discord.ui.Button(
                label="Confirm",
                style=discord.ButtonStyle.secondary,
                custom_id="number_roulette_confirm",
                disabled=True,
            )
            self._confirm_button.callback = self.confirm_number
            self._buttons["confirm"] = self._confirm_button

        cancel = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_cancel",
        )
        cancel.callback = self.cancel
        self._buttons["cancel"] = cancel

        elements = [self._panel_text]
        if self._art_filename is not None:
            elements.append(
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{self._art_filename}",
                        description=f"{self.game_title} artwork",
                    )
                )
            )
        elements.append(Separator())
        if self.game_type == "number_roulette":
            elements.append(ActionRow(number_selects[0]))
            elements.append(ActionRow(number_selects[1]))
            elements.append(ActionRow(self._confirm_button, cancel))
        elif len(choice_buttons) <= 2:
            elements.append(ActionRow(*choice_buttons, cancel))
        else:
            split = (len(choice_buttons) + 1) // 2
            elements.append(ActionRow(*choice_buttons[:split]))
            elements.append(ActionRow(*choice_buttons[split:], cancel))
        self.add_item(
            Container(
                *elements,
                accent_color=_SOLO_GAME_ACCENTS.get(self.game_type, THEME["teal"]),
            )
        )

    def _waiting_content(self) -> str:
        content = f"# {self.game_title}\n**Bet:** {self.amount:,} {self.coin_emoji}"
        if self.instructions:
            content += f"\n{self.instructions}"
        if self.odds_description:
            content += f"\n**Chance:** {self.odds_description}"
        if self.payout_description:
            content += f"\n-# {self.payout_description}"
        if self.game_type == "number_roulette":
            selected = self.selected_number if self.selected_number is not None else "—"
            content += f"\n**Selected:** {selected}"
        return content

    def _art_file(self):
        if self._art_filename is None or self._art_bytes is None:
            return None
        return discord.File(io.BytesIO(self._art_bytes), filename=self._art_filename)

    def _update_result_art(self, choice: str, result: str, outcome: str) -> None:
        if self._art_filename is None:
            return
        won = True if outcome == "win" else False if outcome == "loss" else None
        if self.game_type in {"plinko", "number_roulette"}:
            self._art_bytes = _render_advanced_instant_art(
                self.game_type,
                choice=choice,
                result=result,
                won=won,
            )
        else:
            self._art_bytes = _render_solo_game_art(
                self.game_type,
                choice=choice if self.game_type in {"coinflip", "dice"} else None,
                result=result,
                won=won,
            )

    def _disable_buttons(self) -> None:
        for button in self._buttons.values():
            button.disabled = True

    def _render_result_panel(self) -> None:
        self.clear_items()
        self._panel_text = TextDisplay(self._panel_text.content)
        self._replay_button = discord.ui.Button(
            label="Play Again",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{self.game_type}_replay",
        )
        self._replay_button.callback = self.replay
        elements = [self._panel_text]
        if self._art_filename is not None:
            elements.append(
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{self._art_filename}",
                        description=f"{self.game_title} result",
                    )
                )
            )
        elements.extend((Separator(), ActionRow(self._replay_button)))
        self.add_item(
            Container(
                *elements,
                accent_color=_SOLO_GAME_ACCENTS.get(self.game_type, THEME["teal"]),
            )
        )
        self._buttons = {"replay": self._replay_button}

    async def confirm_number(self, interaction: discord.Interaction) -> None:
        if self.selected_number is None:
            return await interaction.response.send_message(
                view=StatusPanel("Choose a number", "Select a number first.", "warning"),
                ephemeral=True,
            )
        await self.choose(interaction, side=self.selected_number)

    async def replay(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                view=StatusPanel("Not your game", "Only the original player can replay.", "warning"),
                ephemeral=True,
            )
        if not self.done or self.context is None:
            return await interaction.response.send_message(
                view=StatusPanel("Game closed", "Start a new game with the command.", "warning"),
                ephemeral=True,
            )
        try:
            balance = await asyncio.to_thread(bank.get_balance, int(self.requester.id))
        except Exception:
            logger.exception("Could not verify replay balance user_id=%s", self.requester.id)
            return await interaction.response.send_message(
                view=StatusPanel("Balance unavailable", "Try again in a moment.", "warning"),
                ephemeral=True,
            )
        if balance < self.amount:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "Not enough coins",
                    f"Balance: {balance:,} {self.coin_emoji} · Bet: {self.amount:,} {self.coin_emoji}",
                    "warning",
                ),
                ephemeral=True,
            )
        replay_view = SoloWagerView(
            self.cog,
            self.requester,
            self.amount,
            game_type=self.game_type,
            game_title=self.game_title,
            choices=self.choices,
            payout_numerator=self.payout_numerator,
            payout_denominator=self.payout_denominator,
            odds_description=self.odds_description,
            instructions=self.instructions,
            payout_description=self.payout_description,
            outcome_resolver=self.outcome_resolver,
            context=self.context,
        )
        if not claim_solo_session(self.requester.id, replay_view):
            replay_view.stop()
            return await interaction.response.send_message(
                view=StatusPanel("Game active", "Finish your current solo game first.", "warning"),
                ephemeral=True,
            )
        replay_view.message = interaction.message or self.message
        replay_file = replay_view._art_file()
        try:
            await interaction.response.edit_message(
                view=replay_view,
                attachments=[replay_file] if replay_file is not None else [],
            )
            self.stop()
        except asyncio.CancelledError:
            release_solo_session(self.requester.id, replay_view)
            raise
        except Exception:
            release_solo_session(self.requester.id, replay_view)
            logger.exception("Could not restart solo game type=%s", self.game_type)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    view=StatusPanel("Replay unavailable", "Start a new game with the command.", "warning"),
                    ephemeral=True,
                )

    async def _edit_message(self) -> bool:
        if self.message is None:
            return False
        try:
            if self._art_filename is not None:
                await self.message.edit(view=self, attachments=[self._art_file()])
            else:
                await self.message.edit(view=self)
            return True
        except discord.NotFound:
            logger.info("Solo wager panel was deleted game_id=%s", self.game_id)
        except discord.HTTPException:
            logger.warning("Could not update solo wager panel game_id=%s", self.game_id, exc_info=True)
        return False

    def _release_session(self) -> None:
        release_solo_session(self.requester.id, self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "Not your game",
                    "Only the original player can play this round.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def choose(self, interaction: discord.Interaction, side: str) -> None:
        if self.done or self.resolving:
            return await interaction.response.send_message(
                view=StatusPanel("Game closed", "This round has already ended.", "warning"),
                ephemeral=True,
            )
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                view=StatusPanel("Not your game", "Only the original player can play this round.", "warning"),
                ephemeral=True,
            )

        self.resolving = True
        self.message = interaction.message or self.message
        self._disable_buttons()
        try:
            await interaction.response.defer()
        except asyncio.CancelledError:
            self.done = True
            self.resolving = False
            self._disable_buttons()
            self._release_session()
            self.stop()
            raise
        except (discord.HTTPException, discord.InteractionResponded):
            logger.exception(
                "Could not acknowledge solo wager choice game_type=%s requester_id=%s",
                self.game_type,
                self.requester.id,
            )
            await self._finish_without_bet(
                "# Could not start\nNo bet was placed. Try again."
            )
            return

        game_id = uuid.uuid4().hex
        try:
            if self.outcome_resolver is None:
                result_side = secrets.choice(tuple(self.choices))
                won = side == result_side
                payout = _gross_payout(
                    self.amount, self.payout_numerator, self.payout_denominator
                ) if won else 0
            else:
                result_side, won, payout = self.outcome_resolver(side, self.amount)
        except Exception:
            logger.exception("Could not calculate solo wager outcome game_type=%s", self.game_type)
            await self._finish_without_bet(
                "# Result unavailable\n"
                "No bet was placed. Try again."
            )
            return
        self.game_id = game_id

        try:
            wager_args = (
                game_id,
                self.requester.id,
                self.game_type,
                self.amount,
                side,
                result_side,
                won,
                payout,
            )
            channel_id = getattr(interaction, "channel_id", None)
            message_id = getattr(self.message, "id", None)
            if isinstance(channel_id, int) and isinstance(message_id, int):
                receipt = await asyncio.to_thread(
                    bank.play_casino_wager,
                    *wager_args,
                    channel_id=channel_id,
                    message_id=message_id,
                )
            else:
                receipt = await asyncio.to_thread(
                    bank.play_casino_wager,
                    *wager_args,
                )
        except asyncio.CancelledError:
            # The database operation may have committed before cancellation arrived.
            # Instant games never commit an active wager, so lookup cannot refund a win.
            self.cog._schedule_recovery(
                game_id, force_refund=False, callback=self._recovered
            )
            raise
        except Exception:
            logger.exception("Could not confirm atomic solo wager game_id=%s", game_id)
            self._panel_text.content = (
                "# PROCESSING\n"
                "Finalizing this transaction..."
            )
            await self._edit_message()
            self.cog._schedule_recovery(
                game_id, force_refund=False, callback=self._recovered
            )
            return

        if not receipt.get("created"):
            reason = receipt.get("reason")
            if reason == "insufficient_funds":
                balance = int(receipt.get("balance", 0))
                message = f"Balance: **{balance:,} {self.coin_emoji}** · Bet: **{self.amount:,} {self.coin_emoji}**."
            elif reason == "duplicate_game":
                message = "This round was already recorded."
            else:
                message = "The bet could not be accepted. No coins were taken."
            return await self._finish_without_bet("# Bet declined\n" + message)

        if not receipt.get("settled"):
            # Defensive fail-closed guard: an instant bet must return a terminal receipt.
            self._panel_text.content = (
                "# PROCESSING\n"
                "Finalizing this transaction..."
            )
            await self._edit_message()
            self.cog._schedule_recovery(
                game_id, force_refund=False, callback=self._recovered
            )
            return

        await self._finalize(receipt)

    async def _recover_after_component_error(self, incident_id: str) -> None:
        """Resolve a callback fault without refunding a successfully settled instant bet."""
        self.resolving = True
        self._disable_buttons()
        if self.game_id is None:
            await self._finish_without_bet(
                "# Game closed\n"
                f"Reference: {incident_id} · No bet was placed."
            )
            return

        self._panel_text.content = (
            "# Checking bet\n"
            f"Reference: {incident_id}\n"
            "Verifying the same transaction."
        )
        self.cog._schedule_recovery(
            self.game_id,
            force_refund=False,
            callback=self._recovered,
        )
        await self._edit_message()

    async def _finish_without_bet(self, content: str) -> None:
        self.done = True
        self.resolving = False
        self._panel_text.content = content
        self._disable_buttons()
        try:
            await self._edit_message()
        finally:
            self._release_session()
            self.stop()

    async def _recovered(self, result: dict) -> None:
        if result.get("settled"):
            await self._finalize(result)
        elif result.get("reason") == "not_found":
            await self._finish_without_bet(
                "# Bet not confirmed\n"
                "No result was announced. Check your balance before retrying."
            )

    async def _finalize(self, result: dict) -> None:
        self.done = True
        self.resolving = False
        self._disable_buttons()
        choice = str(result.get("choice", ""))
        result_side = str(result.get("result", ""))
        choice_label = self.choices.get(choice, {}).get("label", choice)
        result_label = self.choices.get(result_side, {}).get("label", result_side)
        if self.game_type == "plinko" and result_side.startswith("slot="):
            try:
                parts = dict(part.split("=", 1) for part in result_side.split(";") if "=" in part)
                result_label = f"Bin {int(parts['slot']) + 1} · {parts.get('multiplier', '0x')}"
            except (KeyError, ValueError):
                result_label = "Ball dropped"
        balance_after = result.get("balance_after")
        balance_line = (
            f"\n**Balance:** {int(balance_after):,} {self.coin_emoji}"
            if balance_after is not None
            else ""
        )
        outcome = result.get("outcome", "loss")
        stake = int(result.get("stake", self.amount))
        bonus = max(0, int(result.get("payout", 0)) - stake)
        self._update_result_art(choice, result_side, outcome)

        if result.get("status") == "refunded" or outcome == "balance_limit":
            self._panel_text.content = (
                f"# REFUNDED · {self.game_title}\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0"
                f"{balance_line}"
            )
        elif outcome == "win":
            self._panel_text.content = (
                f"# WIN · {self.game_title}\n"
                f"Pick: **{choice_label}** · Result: **{result_label}**\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: **+{bonus:,} {self.coin_emoji}**"
                f"{balance_line}"
            )
        elif outcome == "push":
            self._panel_text.content = (
                f"# PUSH · {self.game_title}\n"
                f"Pick: **{choice_label}** · Result: **{result_label}**\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0"
                f"{balance_line}"
            )
        else:
            self._panel_text.content = (
                f"# LOSS · {self.game_title}\n"
                f"Pick: **{choice_label}** · Result: **{result_label}**\n"
                f"Bet: {stake:,} {self.coin_emoji} · Bonus: +0"
                f"{balance_line}"
            )

        self._render_result_panel()
        try:
            updated = await self._edit_message()
            self.result_published = updated
            if not updated:
                logger.error("Could not publish settled solo result in-place game_id=%s", self.game_id)
        finally:
            self._release_session()

    async def cancel(self, interaction: discord.Interaction) -> None:
        if self.done or self.resolving:
            return await interaction.response.send_message(
                view=StatusPanel("Game closed", "This round can no longer be cancelled.", "warning"),
                ephemeral=True,
            )
        self.message = interaction.message or self.message
        self.done = True
        self._disable_buttons()
        self._panel_text.content = (
            f"# CANCELLED · {self.game_title}\n"
            "No bet was placed."
        )
        try:
            await interaction.response.edit_message(view=self)
        except asyncio.CancelledError:
            raise
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning(
                "Could not cancel solo game through its interaction game_type=%s",
                self.game_type,
                exc_info=True,
            )
            await self._edit_message()
        finally:
            self._release_session()
            self.stop()

    async def on_timeout(self) -> None:
        if self.done:
            if self._replay_button is not None:
                self._replay_button.disabled = True
                await self._edit_message()
            self.stop()
            return
        if self.resolving:
            return
        self.done = True
        self._disable_buttons()
        self._panel_text.content = (
            f"# EXPIRED · {self.game_title}\n"
            "No bet was placed."
        )
        try:
            await self._edit_message()
        finally:
            self._release_session()
            self.stop()


class SoloBetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._wagers_ready = False

    async def cog_load(self):
        try:
            refunded, active_ids = await recover_orphaned_casino_wagers(
                self.bot,
                self._schedule_recovery,
            )
        except Exception:
            self._wagers_ready = False
            logger.exception(
                "Solo casino wager recovery could not be verified; disabling new wagers"
            )
            return

        self._wagers_ready = True
        if refunded:
            logger.warning("Refunded %s interrupted solo casino wager(s) during startup", refunded)
        if active_ids:
            logger.warning("Scheduled recovery for %s unresolved solo casino wager(s)", len(active_ids))

    async def _settle_with_retries(self, game_id: str, *, force_refund: bool = False) -> dict:
        for attempt in range(3):
            try:
                return await asyncio.to_thread(
                    bank.settle_casino_wager,
                    game_id,
                    force_refund=force_refund,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Solo casino wager settlement failed game_id=%s attempt=%s/3",
                    game_id,
                    attempt + 1,
                )
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
        return {"settled": False, "reason": "database_error"}

    def _schedule_recovery(self, game_id: str, *, force_refund: bool, callback=None):
        game_id = str(game_id)
        existing = _RECOVERY_TASKS.get(game_id)
        if existing is not None and not existing.done():
            return existing

        async def worker():
            attempt = 0
            try:
                while True:
                    await asyncio.sleep(_RECOVERY_DELAYS_SECONDS[min(attempt, len(_RECOVERY_DELAYS_SECONDS) - 1)])
                    result = await self._settle_with_retries(game_id, force_refund=force_refund)
                    if result.get("settled") or result.get("reason") == "not_found":
                        if callback is not None:
                            try:
                                await callback(result)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Could not publish recovered coinflip result game_id=%s", game_id)
                        return
                    attempt += 1
                    if attempt % 3 == 0:
                        logger.error(
                            "Solo casino wager remains unresolved game_id=%s reason=%s attempts=%s",
                            game_id,
                            result.get("reason", "unknown"),
                            attempt,
                        )
            finally:
                current = asyncio.current_task()
                if _RECOVERY_TASKS.get(game_id) is current:
                    _RECOVERY_TASKS.pop(game_id, None)

        task = asyncio.create_task(worker())
        _RECOVERY_TASKS[game_id] = task
        return task

    async def _start_wager(
        self,
        ctx,
        bet: str,
        *,
        game_type: str,
        game_title: str,
        choices: dict,
        payout_numerator: int,
        payout_denominator: int = 100,
        odds_description: str,
        instructions: str,
        command_name: str,
        payout_description: str | None = None,
        outcome_resolver: Callable[[str, int], tuple[str, bool, int]] | None = None,
    ):
        if not self._wagers_ready:
            return await ctx.send(
                view=StatusPanel(
                    "Bets unavailable",
                    "Wagers are being checked. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        amount = parse_bet_amount(bet, max_bet=MAX_BET)
        if not MIN_BET <= amount <= MAX_BET:
            usage = f"/{command_name} bet:100" if ctx.interaction is not None else f"{ctx.prefix}{command_name} 100"
            return await ctx.send(
                view=StatusPanel(
                    "Invalid bet",
                    f"Use {usage}. Bet: {MIN_BET:,}–{MAX_BET:,} {get_coin_emoji(ctx.guild)}.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        if has_active_solo_session(ctx.author.id):
            return await ctx.send(
                view=StatusPanel(
                    "Game active",
                    "Finish or cancel your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        try:
            balance = await asyncio.to_thread(bank.get_balance, int(ctx.author.id))
        except Exception:
            logger.exception(
                "Could not verify solo wager balance requester_id=%s game_type=%s",
                ctx.author.id,
                game_type,
            )
            return await ctx.send(
                view=StatusPanel(
                    "Balance unavailable",
                    "No bet was placed. Try again shortly.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        if balance < amount:
            return await ctx.send(
                view=StatusPanel(
                    "Not enough coins",
                    f"Balance: **{balance:,} {get_coin_emoji(ctx.guild)}** · Bet: **{amount:,} {get_coin_emoji(ctx.guild)}**.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        view = SoloWagerView(
            self,
            ctx.author,
            amount,
            game_type=game_type,
            game_title=game_title,
            choices=choices,
            payout_numerator=payout_numerator,
            payout_denominator=payout_denominator,
            odds_description=odds_description,
            instructions=instructions,
            payout_description=payout_description,
            outcome_resolver=outcome_resolver,
            context=ctx,
        )
        if not claim_solo_session(ctx.author.id, view):
            view.stop()
            return await ctx.send(
                view=StatusPanel(
                    "Game active",
                    "Finish or cancel your current solo game first.",
                    "warning",
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )
        try:
            if view._art_filename is not None:
                view.message = await ctx.send(view=view, file=view._art_file())
            else:
                view.message = await ctx.send(view=view)
        except asyncio.CancelledError:
            view._release_session()
            raise
        except Exception:
            view._release_session()
            logger.exception(
                "Could not create solo wager session type=%s requester_id=%s",
                game_type,
                ctx.author.id,
            )
            raise

    @commands.hybrid_command(
        name="coinflip",
        aliases=["flip", "cf", "coin", "coin-flip", "عملة", "رمي_العملة"],
        description="راهن بعملة افتراضية على نتيجة رمي العملة",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def coinflip(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="coinflip",
            game_title="COIN FLIP",
            choices=SIDES,
            payout_numerator=190,
            odds_description="50%",
            instructions="Choose heads or tails.",
            command_name="coinflip",
            payout_description="Bonus: +0.9x.",
        )

    @commands.hybrid_command(
        name="dice",
        aliases=["نرد", "زهر", "roll", "die"],
        description="راهن بعملة افتراضية على رقم النرد",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def dice(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="dice",
            game_title="DICE",
            choices=DICE_CHOICES,
            payout_numerator=570,
            odds_description="16.67%",
            instructions="Pick a number from 1 to 6.",
            payout_description="Bonus: +4.7x.",
            command_name="dice",
        )

    @commands.hybrid_command(
        name="slots",
        aliases=["slot", "سلوت", "ماكينة", "spin"],
        description="لف ماكينة الحظ مقابل رهان بعملة افتراضية",
    )
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def slots(self, ctx, bet: str):
        return await self._start_wager(
            ctx,
            bet,
            game_type="slots",
            game_title="SLOTS",
            choices=SLOT_CHOICES,
            payout_numerator=170,
            odds_description="44.44% any win",
            instructions="Spin 3 reels.",
            command_name="slots",
            payout_description="Pair: +0.7x · Trips: +4x–14x bonus.",
            outcome_resolver=_spin_slots,
        )


async def setup(bot):
    await bot.add_cog(SoloBetting(bot))
