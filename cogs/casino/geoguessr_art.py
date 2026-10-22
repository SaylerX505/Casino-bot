"""Image rendering helpers for GeoGuessr. No Discord game-state logic lives here."""

import io
import logging
import math

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)


def draw_dashed_line(draw, pt1, pt2, fill, width, dash_length=15):
    x1, y1 = pt1
    x2, y2 = pt2
    distance = math.hypot(x2 - x1, y2 - y1)
    if distance == 0: return
    
    segments = int(distance / dash_length)
    if segments == 0:
        draw.line([(x1, y1), (x2, y2)], fill=fill, width=width)
        return
        
    for i in range(segments):
        if i % 2 == 0:
            start_x = x1 + (x2 - x1) * (i / segments)
            start_y = y1 + (y2 - y1) * (i / segments)
            end_x = x1 + (x2 - x1) * ((i + 1) / segments)
            end_y = y1 + (y2 - y1) * ((i + 1) / segments)
            draw.line([(start_x, start_y), (end_x, end_y)], fill=fill, width=width)

async def generate_result_map(actual_coords, player_data_list):
    try:
        base_map = Image.open("world_map.jpg").convert("RGBA")
    except FileNotFoundError:
        base_map = Image.new('RGBA', (1024, 512), color='grey') 
        
    width, height = base_map.size
    draw = ImageDraw.Draw(base_map)

    def get_xy(lat, lon):
        x = (lon + 180) * (width / 360)
        y = (90 - lat) * (height / 180)
        return x, y

    cx, cy = get_xy(actual_coords[0], actual_coords[1])
    colors = ["#00f5d4", "#ff595e", "#ffca3a", "#8ac926", "#f15bb5", "#1982c4", "#6a4c93", "#f4a261"]
    all_x = [cx]
    all_y = [cy]

    async with aiohttp.ClientSession() as session:
        for i, player_data in enumerate(player_data_list):
            if not player_data['coords']: continue 
            px, py = get_xy(player_data['coords'][0], player_data['coords'][1])
            
            all_x.append(px)
            all_y.append(py)
            
            line_color = colors[i % len(colors)]
            player_data['line_color'] = line_color 
            draw_dashed_line(draw, (px, py), (cx, cy), fill=line_color, width=3, dash_length=15)

        draw.ellipse((cx-8, cy-8, cx+8, cy+8), fill="red", outline="white", width=2)
        draw.arc([cx-14, cy-14, cx+14, cy+14], 0, 360, fill="red", width=2)

        for player_data in player_data_list:
            if not player_data['coords']: continue 
            px, py = get_xy(player_data['coords'][0], player_data['coords'][1])
            line_color = player_data.get('line_color', "white")

            try:
                async with session.get(player_data['avatar_url']) as a_resp:
                    if a_resp.status == 200:
                        avatar_bytes = await a_resp.read()
                        avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((40, 40))
                        mask = Image.new("L", (40, 40), 0)
                        ImageDraw.Draw(mask).ellipse((0, 0, 40, 40), fill=255)
                        base_map.paste(avatar_img, (int(px-20), int(py-20)), mask)
                        draw.arc([px-22, py-22, px+22, py+22], 0, 360, fill=line_color, width=3)
            except Exception:
                logger.debug("Optional GeoGuessr enhancement failed", exc_info=True)
    margin = 150
    min_x = max(0, min(all_x) - margin)
    max_x = min(width, max(all_x) + margin)
    min_y = max(0, min(all_y) - margin)
    max_y = min(height, max(all_y) + margin)
    
    min_crop_width = 500
    min_crop_height = 400
    
    if (max_x - min_x) < min_crop_width:
        center_x = (min_x + max_x) / 2
        min_x = max(0, center_x - min_crop_width / 2)
        max_x = min(width, center_x + min_crop_width / 2)
        
    if (max_y - min_y) < min_crop_height:
        center_y = (min_y + max_y) / 2
        min_y = max(0, center_y - min_crop_height / 2)
        max_y = min(height, center_y + min_crop_height / 2)
        
    cropped_map = base_map.crop((int(min_x), int(min_y), int(max_x), int(max_y)))

    out_bytes = io.BytesIO()
    cropped_map.save(out_bytes, format='PNG')
    out_bytes.seek(0)
    return out_bytes

async def generate_dashboard_image(players_data, round_num, total_rounds):
    width, height = 1000, 750
    bg_color = (32, 34, 37)
    board = Image.new('RGBA', (width, height), bg_color)
    
    try:
        bg_img = Image.open("background.png").convert("RGBA")
        bg_img = bg_img.resize((width, height), Image.Resampling.LANCZOS)
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=8))
        r, g, b, a = bg_img.split()
        a = a.point(lambda p: p * 0.15)
        bg_img = Image.merge('RGBA', (r, g, b, a))
        board.paste(bg_img, (0, 0), bg_img)
    except Exception:
        logger.debug("Optional GeoGuessr enhancement failed", exc_info=True)
    draw = ImageDraw.Draw(board)
    try:
        font_large = ImageFont.truetype("arial.ttf", 40)
        font_medium = ImageFont.truetype("arial.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        logger.debug("GeoGuessr image fonts are unavailable; using the default font", exc_info=True)
        font_large = font_medium = font_small = ImageFont.load_default()

    draw.text((width//2, 40), f"Round {round_num} / {total_rounds}", fill="white", font=font_large, anchor="mm")

    box_w, box_h = 160, 180
    start_x, start_y = 60, 100
    spacing_x, spacing_y = 180, 200

    async with aiohttp.ClientSession() as session:
        for idx, p_data in enumerate(players_data):
            row = idx // 5
            col = idx % 5
            x = start_x + (col * spacing_x)
            y = start_y + (row * spacing_y)
            
            card_color = (47, 49, 54, 220) 
            overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rounded_rectangle([x, y, x+box_w, y+box_h], radius=15, fill=card_color)
            board.alpha_composite(overlay)
            
            gained = p_data['gained']
            gained_text = f"+{gained}" if gained > 0 else "+0"
            badge_color = "lime" if gained == 10 else ("orange" if gained in [5, 6] else ("gold" if gained == 3 else "grey"))
            draw.rounded_rectangle([x+box_w-45, y-10, x+box_w+5, y+25], radius=8, fill=badge_color)
            draw.text((x+box_w-20, y+7), gained_text, fill="black", font=font_small, anchor="mm")
            
            draw.rounded_rectangle([x+20, y+box_h-40, x+box_w-20, y+box_h-10], radius=10, fill=(32, 34, 37), outline=badge_color, width=2)
            draw.text((x+box_w//2, y+box_h-25), str(p_data['score']), fill="white", font=font_medium, anchor="mm")
            
            name = p_data['user'].display_name[:12]
            draw.text((x+box_w//2, y+box_h-65), name, fill="white", font=font_medium, anchor="mm")
            
            try:
                async with session.get(p_data['avatar_url']) as a_resp:
                    if a_resp.status == 200:
                        av_bytes = await a_resp.read()
                        av_img = Image.open(io.BytesIO(av_bytes)).convert("RGBA").resize((80, 80))
                        mask = Image.new("L", (80, 80), 0)
                        ImageDraw.Draw(mask).ellipse((0, 0, 80, 80), fill=255)
                        board.paste(av_img, (x+box_w//2-40, y+15), mask)
            except Exception:
                logger.debug("Optional GeoGuessr enhancement failed", exc_info=True)
    out_bytes = io.BytesIO()
    board.save(out_bytes, format='PNG')
    out_bytes.seek(0)
    return out_bytes
