"""Static artwork renderers shared by the solo casino games."""

import io
import math

from PIL import Image, ImageDraw, ImageFont


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
