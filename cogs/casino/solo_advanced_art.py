"""Artwork renderer for Mines and Hi-Lo."""

import io
from PIL import Image, ImageDraw

from cogs.casino.solo_advanced_core import MINES_TILE_COUNT
from cogs.casino.solo_betting_art import _art_font


def _hilo_rank_label(rank: int) -> str:
    return {11: "J", 12: "Q", 13: "K", 14: "A"}.get(rank, str(rank))


def _render_live_art(
    game_type: str,
    *,
    revealed: set[int] | None = None,
    mines: set[int] | None = None,
    reveal_mines: bool = False,
    mines_count: int = 3,
    multiplier: float = 1.0,
    current_rank: int | None = None,
    previous_rank: int | None = None,
    current_suit: str = "♠",
    previous_suit: str = "♠",
    final: bool = False,
    won: bool | None = None,
) -> bytes:
    """Create the game-specific dark, result-aware game banner."""
    width, height = 1200, 420
    palette = {
        "mines": ((18, 37, 34), (8, 15, 20), (87, 210, 166), "MINES"),
        "hilo": ((27, 29, 51), (8, 12, 22), (191, 174, 255), "HI-LO"),
    }
    top, bottom, accent, title = palette[game_type]
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
    title_size = 43
    title_font = _art_font(title_size, bold=True)
    while draw.textbbox((0, 0), title, font=title_font)[2] > 300 and title_size > 28:
        title_size -= 1
        title_font = _art_font(title_size, bold=True)
    draw.text((68, 82), title, font=title_font, fill=(245, 245, 239))
    draw.line((68, 145, 355, 145), fill=accent, width=4)

    if game_type == "mines":
        revealed = revealed or set()
        mines = mines or set()
        draw.text((64, 181), f"{mines_count} MINES", font=_art_font(23, bold=True), fill=(240, 242, 234))
        draw.text((64, 220), f"{len(revealed)} SAFE", font=_art_font(20, bold=True), fill=accent)
        draw.text((64, 258), "20 TILES", font=_art_font(18, bold=True), fill=(181, 195, 196))
        x0, y0, tile_w, tile_h, gap_x, gap_y = 390, 58, 78, 65, 12, 12
        for index in range(MINES_TILE_COUNT):
            row, col = divmod(index, 5)
            left = x0 + col * (tile_w + gap_x)
            top_y = y0 + row * (tile_h + gap_y)
            is_mine = reveal_mines and index in mines
            is_safe = index in revealed
            if is_mine:
                fill, outline, symbol = (96, 34, 45), (245, 110, 110), "×"
            elif is_safe:
                fill, outline, symbol = (26, 91, 72), accent, "◆"
            else:
                fill, outline, symbol = (24, 34, 45), (65, 81, 94), f"{index + 1:02d}"
            draw.rounded_rectangle(
                (left, top_y, left + tile_w, top_y + tile_h),
                radius=11,
                fill=fill,
                outline=outline,
                width=3 if is_mine or is_safe else 2,
            )
            draw.text(
                (left + tile_w // 2, top_y + tile_h // 2),
                symbol,
                font=_art_font(23 if is_mine or is_safe else 17, bold=True),
                fill=(255, 245, 231) if is_mine or is_safe else (155, 172, 183),
                anchor="mm",
            )
        draw.text((645, 373), "REVEAL · MULTIPLY · CASH OUT", font=_art_font(14, bold=True), fill=accent, anchor="mm")
    else:
        current_rank = current_rank or 8
        draw.text((64, 183), "PREDICT", font=_art_font(17, bold=True), fill=(181, 195, 205))
        draw.text((64, 214), "HIGHER OR LOWER", font=_art_font(16, bold=True), fill=(240, 236, 229))
        draw.text((64, 257), f"STREAK  /  {multiplier:.2f}x", font=_art_font(16, bold=True), fill=accent)
        def card(cx, rank, suit):
            card_left, card_top = cx - 72, 56
            draw.rounded_rectangle((card_left + 7, card_top + 9, card_left + 151, card_top + 290), radius=18, fill=(3, 6, 12))
            draw.rounded_rectangle((card_left, card_top, card_left + 144, card_top + 282), radius=18, fill=(241, 239, 229), outline=(255, 255, 249), width=3)
            label = _hilo_rank_label(rank)
            color = (174, 42, 55) if suit in ("♥", "♦") else (25, 29, 36)
            draw.text((card_left + 17, card_top + 15), f"{label}{suit}", font=_art_font(26, bold=True), fill=color)
            draw.text((card_left + 72, card_top + 138), suit, font=_art_font(58, bold=True), fill=color, anchor="mm")
            draw.text((card_left + 72, card_top + 230), "CKR", font=_art_font(14, bold=True), fill=(97, 103, 109), anchor="mm")
        if previous_rank is not None and final:
            card(545, previous_rank, previous_suit)
            card(775, current_rank, current_suit)
            draw.text((660, 198), "›", font=_art_font(56, bold=True), fill=accent, anchor="mm")
        else:
            card(665, current_rank, current_suit)
            draw.rounded_rectangle((888, 120, 1137, 300), radius=21, fill=(19, 24, 34), outline=accent, width=3)
            draw.text((1012, 158), "CURRENT CARD", font=_art_font(14, bold=True), fill=(177, 190, 195), anchor="mm")
            draw.text((1012, 208), _hilo_rank_label(current_rank), font=_art_font(40, bold=True), fill=(248, 245, 237), anchor="mm")
            draw.text((1012, 257), "HIGHER  /  LOWER", font=_art_font(13, bold=True), fill=accent, anchor="mm")

    if won is not None:
        badge_color = (48, 166, 112) if won else (170, 48, 58)
        draw.rounded_rectangle((58, 325, 314, 370), radius=13, fill=badge_color)
        draw.text((186, 347), "WIN" if won else "NO WIN", font=_art_font(19, bold=True), fill=(255, 255, 248), anchor="mm")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
