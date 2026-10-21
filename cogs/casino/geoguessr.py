import asyncio
import io
import json
import logging
import math
import os
import random
import time
from functools import partial

import aiohttp
import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from cogs.casino.game_registry import (
    is_active_game,
    owns_game_task,
    register_game_task,
    release_game_task,
    set_game_view,
)
from cogs.casino.permissions import is_staff_or_admin
from cogs.utility.theme import (
    embed_to_v2,
    THEME,
    StatusPanel,
    lobby_embed,
    report_game_failure,
    result_embed,
)

logger = logging.getLogger(__name__)


class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Developed by @neglawy", style=discord.ButtonStyle.secondary, disabled=True))

DATABASE_FILE = "data/database.json"
USED_TRACKER_FILE = "data/used_images.json"
IMAGES_DIR = "images"
SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"

geolocator = Nominatim(user_agent="discord_geoguess_cogs_bot")

def load_database():
    os.makedirs(os.path.dirname(DATABASE_FILE) or ".", exist_ok=True)
    try:
        with open(DATABASE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        logger.error("GeoGuessr static database is missing path=%s", DATABASE_FILE)
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load GeoGuessr static database path=%s", DATABASE_FILE)
        return {}

    if not isinstance(payload, (dict, list)):
        logger.error("GeoGuessr database has an unsupported top-level type path=%s", DATABASE_FILE)
        return {}
    return payload


def load_used_images():
    os.makedirs(os.path.dirname(USED_TRACKER_FILE) or ".", exist_ok=True)
    try:
        with open(USED_TRACKER_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return set()
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load GeoGuessr used-image tracker; starting with an empty tracker")
        return set()

    if not isinstance(payload, list):
        logger.error("GeoGuessr used-image tracker is not a list; starting with an empty tracker")
        return set()
    return {item for item in payload if isinstance(item, str)}


def save_used_images(used_set):
    """Atomically replace the generated image tracker so crashes cannot truncate it."""
    tracker_path = os.path.abspath(USED_TRACKER_FILE)
    parent_dir = os.path.dirname(tracker_path)
    os.makedirs(parent_dir, exist_ok=True)
    temporary_path = f"{tracker_path}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(sorted({item for item in used_set if isinstance(item, str)}), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, tracker_path)
    except OSError:
        logger.exception("Could not persist GeoGuessr used-image tracker path=%s", tracker_path)
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            logger.debug("Could not remove incomplete GeoGuessr tracker temp file", exc_info=True)
        raise

game_db = load_database()

def get_flag_emoji(country_code):
    if not country_code or len(country_code) != 2: return "❓"
    return chr(ord(country_code[0].upper()) + 127397) + chr(ord(country_code[1].upper()) + 127397)

def get_guess_data(guess_text):
    try:
        location = geolocator.geocode(
            guess_text, language='en,ar', addressdetails=True, featuretype='country', timeout=10
        )
        if location and 'address' in location.raw:
            code = location.raw['address'].get('country_code', '').lower()
            return (location.latitude, location.longitude), code
    except Exception: pass
    return None, None

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
            except Exception: pass

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
    except Exception: pass

    draw = ImageDraw.Draw(board)
    try:
        font_large = ImageFont.truetype("arial.ttf", 40)
        font_medium = ImageFont.truetype("arial.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except Exception:
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
            except Exception: pass

    out_bytes = io.BytesIO()
    board.save(out_bytes, format='PNG')
    out_bytes.seek(0)
    return out_bytes

# --- CORE GAME LOOP ---
async def run_game_loop(ctx, players):
    try:
        scores = {player: 0 for player in players}
        total_rounds = 6

        used_images = load_used_images()
        available_keys = set(game_db.keys())
        unused_keys = list(available_keys - used_images)

        if len(unused_keys) < total_rounds:
            await ctx.send(
                view=StatusPanel(
                    "إعادة تعيين الصور",
                    "تم لعب كل الصور المتاحة؛ هنبدأ دورة جديدة.",
                    "warning",
                )
            )
            used_images = set()
            unused_keys = list(available_keys)

        if len(unused_keys) < total_rounds:
            return await ctx.send(
                view=StatusPanel(
                    "صور غير كافية",
                    "قاعدة البيانات لا تحتوي على صور كافية لبدء جولة كاملة.",
                    "warning",
                )
            )

        selected_rounds = random.sample(unused_keys, total_rounds)
        used_images.update(selected_rounds)
        save_used_images(used_images)

        for round_num, db_key in enumerate(selected_rounds, 1):
            round_msg = await ctx.send(
                view=StatusPanel(
                    f"الجولة {round_num} من {total_rounds}",
                    "بنجهز صورة الجولة وبيانات الدولة...",
                    "neutral",
                )
            )

            round_data = game_db[db_key]
            actual_country_code = round_data['country_code']
            actual_country_ar = round_data['country_ar']
            actual_street_coords = round_data['coords']
            image_file_path = os.path.join(IMAGES_DIR, round_data['filename'])

            actual_country_center_coords, _ = await asyncio.to_thread(get_guess_data, actual_country_code)
            if not actual_country_center_coords:
                actual_country_center_coords = actual_street_coords

            await asyncio.sleep(1)
            await round_msg.delete()

            try:
                file = discord.File(fp=image_file_path, filename="streetview.png")
            except FileNotFoundError:
                await round_msg.edit(
                    view=StatusPanel(
                        "الصورة غير موجودة",
                        f"الصورة المطلوبة مش موجودة في مجلد {IMAGES_DIR}. هنعدّي الجولة.",
                        "warning",
                    ),
                    attachments=[],
                )
                continue

            timer_state = {"end_time": time.time() + 30.0}
            active_view = ActiveGameView(ctx.author, timer_state, round_num, total_rounds)
            msg = round_msg
            await msg.edit(view=active_view, attachments=[file])
            active_view.message = msg
            set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), active_view)

            guesses = {}
            guess_data_cache = {}
            guess_order = []
            processing_users = set()
            reaction_tasks = set()

            def check(m):
                return m.channel == ctx.channel and m.author in players and m.author not in guesses and m.author not in processing_users

            async def process_reaction(guess_msg, text, received_at):
                try:
                    coords, code = await asyncio.to_thread(get_guess_data, text)
                    if received_at > timer_state["end_time"] or time.time() > timer_state["end_time"] + 5:
                        return
                    if code is None:
                        return
                    if guess_msg.author not in guesses:
                        guess_order.append(guess_msg.author)
                    guesses[guess_msg.author] = text
                    guess_data_cache[guess_msg.author] = {"coords": coords, "code": code, "text": text}
                    flag = get_flag_emoji(code)
                    try:
                        await guess_msg.add_reaction(flag)
                    except discord.HTTPException:
                        await guess_msg.add_reaction("✅")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("GeoGuessr guess processing failed")
                finally:
                    processing_users.discard(guess_msg.author)
            last_update = 30
            while True:
                remaining = int(timer_state["end_time"] - time.time())
                if remaining <= 0 or len(guesses) >= len(players):
                    break
                    
                if remaining % 5 == 0 and remaining != last_update:
                    active_view.update_timer(remaining)
                    try:
                        await msg.edit(view=active_view)
                    except discord.HTTPException:
                        logger.debug("Could not refresh GeoGuessr V2 timer panel", exc_info=True)
                    last_update = remaining

                try:
                    guess_msg = await ctx.bot.wait_for('message', check=check, timeout=1.0)
                    received_at = time.time()
                    if received_at > timer_state["end_time"]:
                        break
                    guess_text = guess_msg.content.strip().lower()
                    processing_users.add(guess_msg.author)
                    task = asyncio.create_task(process_reaction(guess_msg, guess_text, received_at))
                    reaction_tasks.add(task)
                    task.add_done_callback(reaction_tasks.discard)
                except asyncio.TimeoutError:
                    continue

            if reaction_tasks:
                pending = set(reaction_tasks)
                _done, waiting = await asyncio.wait(pending, timeout=5)
                for task in waiting:
                    task.cancel()
                if waiting:
                    await asyncio.gather(*waiting, return_exceptions=True)

            active_view.disable_controls()
            active_view._panel_text.content = (
                f"# انتهت الجولة {round_num}\nتم إغلاق التخمينات. بنحسب النتائج..."
            )
            try:
                await msg.edit(view=active_view)
            except discord.HTTPException:
                logger.warning("GeoGuessr controls could not be disabled", exc_info=True)

            await asyncio.sleep(0.5)

            closest_distance = float('inf')
            best_guess_code = None

            for player in players:
                if player in guesses:
                    data = guess_data_cache.get(player, {'coords': None, 'code': None})
                    if data['coords']:
                        dist = geodesic(actual_country_center_coords, data['coords']).kilometers
                        data['distance'] = dist
                        if dist < closest_distance:
                            closest_distance = dist
                            best_guess_code = data['code']
                    else:
                        data['distance'] = float('inf')

            exact_winners_ordered = [p for p in guess_order if guess_data_cache[p].get('code') == actual_country_code]
            closest_winners_ordered = []
            if not exact_winners_ordered and best_guess_code:
                 closest_winners_ordered = [p for p in guess_order if guess_data_cache[p].get('code') == best_guess_code]
            
            summary_text = f"**الدولة الصحيحة كانت {get_flag_emoji(actual_country_code)} {actual_country_ar.title()}**\n\n**تخمينات اللاعبين:**\n"
            player_results = []

            for player in players:
                gained = 0
                is_correct = False
                
                if player in guesses:
                    data = guess_data_cache[player]
                    guess_code = data['code']
                    guess_coords = data['coords']
                    dist = data.get('distance', float('inf'))
                    is_correct = (guess_code == actual_country_code)
                    
                    if player in exact_winners_ordered:
                        gained = 10 if exact_winners_ordered.index(player) == 0 else 6
                    elif player in closest_winners_ordered:
                        gained = 5 if closest_winners_ordered.index(player) == 0 else 3
                    
                    scores[player] += gained
                    
                    if guess_coords:
                        pts_str = f" **(+{gained} نقطة)**" if gained > 0 else ""
                        summary_text += f"{player.mention}: {get_flag_emoji(guess_code)} {guesses[player].title()} ({dist:,.0f} كم){pts_str}\n"
                    else:
                        summary_text += f"{player.mention}: ❓ إجابة غير صالحة\n"
                else:
                    summary_text += f"{player.mention}: ⏰ لم يخمن\n"
                    
                player_results.append({
                    'user': player, 'score': scores[player], 'gained': gained,
                    'avatar_url': player.display_avatar.url, 'coords': guess_data_cache.get(player, {}).get('coords'), 'correct': is_correct
                })

            await msg.edit(
                view=GeoGuessrResultPanel(
                    "📊 ملخص الجولة",
                    summary_text,
                    outcome="neutral",
                ),
                attachments=[],
            )
            await asyncio.sleep(2) 

            map_bytes = await generate_result_map(actual_country_center_coords, player_results)
            map_file = discord.File(fp=map_bytes, filename="result_map.png")
            await msg.edit(
                view=GeoGuessrResultPanel(
                    "خريطة نتائج الجولة",
                    "أماكن التخمينات بالنسبة للدولة الصحيحة.",
                    outcome="neutral",
                    image_name="result_map.png",
                ),
                attachments=[map_file],
            )
            await asyncio.sleep(3)

            board_bytes = await generate_dashboard_image(player_results, round_num, total_rounds)
            board_file = discord.File(fp=board_bytes, filename="dashboard.png")
            await msg.edit(
                view=GeoGuessrResultPanel(
                    "🏆 لوحة النتائج",
                    "ملخص ترتيب اللاعبين بعد الجولة.",
                    outcome="neutral",
                    image_name="dashboard.png",
                ),
                attachments=[board_file],
            )
            await asyncio.sleep(4) 

        # --- TIE BREAKER ANNOUNCEMENT ---
        final_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_score = final_scores[0][1]
        
        winners = [p for p, s in final_scores if s == top_score]
        
        if len(winners) > 1:
            winner_mentions = " و ".join([w.mention for w in winners])
            desc = f"🏆 **تعادل!** الفائزون هم {winner_mentions} برصيد `{top_score} نقطة`!"
            thumb = SERVER_LOGO_URL
        else:
            winner = winners[0]
            desc = f"🏆 **{winner.mention}** هو الفائز برصيد `{top_score} نقطة`!"
            thumb = winner.display_avatar.url
            
        await msg.edit(
            view=StatusPanel(
                "🏁 انتهت اللعبة!",
                desc,
                "success" if len(winners) == 1 else "neutral",
            ),
            attachments=[],
        )

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("GeoGuessr game loop failed")
        await report_game_failure(
            ctx,
            "توقفت GeoGuessr بسبب خطأ",
            "حصل عطل غير متوقع أثناء الجولة. تم تسجيل الخطأ، وحالة الرهان/الجولة لم يتم تعديلها تلقائيًا.",
        )
    finally:
        release_game_task(ctx.bot, ctx.channel.id, asyncio.current_task())


# --- MENUS AND VIEWS ---
class GuessGameMenu(LayoutView):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = set()
        self.game_started = False
        self.time_left = 40
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام (Join)", "geoguessr_join", "join_button", None),
            ("مغادرة (Leave)", "geoguessr_leave", "leave_button", None),
            ("ابدأ الآن", "geoguessr_start", "start_button", None),
            ("+10 ثواني", "geoguessr_add_time", "add_time", "⏱️"),
        ):
            button = discord.ui.Button(label=label, custom_id=custom_id, style=discord.ButtonStyle.secondary, emoji=emoji)
            button.callback = partial(getattr(self, callback_name), button=button)
            self._buttons.append(button)
        self.add_item(Container(self._panel_text, Separator(), ActionRow(*self._buttons), accent_color=THEME["gold"]))
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        embed = lobby_embed(
            "Waiting room الحفره",
            "اضغط على 'انضمام' للعب.",
            sorted(self.players, key=lambda player: player.id),
            self.time_left,
            max_players=15,
        )
        embed.color = discord.Color.gold()
        embed.set_thumbnail(url=SERVER_LOGO_URL)
        return embed

    def _panel_content(self):
        embed = self.get_embed()
        return f"# {embed.title or 'GeoGuessr'}\n{embed.description or ''}\n\n-# كازينو الحفرة • {config.BRAND}"[:4000]

    def _refresh_panel(self, embed=None):
        embed = embed or self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        description = f"{embed.description}\n\n" if embed.description else ""
        footer = f"-# كازينو الحفرة • {config.BRAND}"
        self._panel_text.content = (title + description + footer)[:4000]

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _send_private_embed(self, interaction, embed):
        await interaction.response.send_message(
            view=StatusPanel(embed.title or "تنبيه", embed.description or "حصل خطأ غير متوقع.", "warning"),
            ephemeral=True,
        )

    async def run_timer(self):
        while self.time_left > 0 and not self.game_started:
            await asyncio.sleep(5)
            if self.game_started: break
            self.time_left -= 5
            if self.time_left <= 0: await self.auto_start()
            elif self.message:
                try:
                    self._refresh_panel()
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    logger.debug("Could not refresh GeoGuessr lobby", exc_info=True)

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) == 0:
            self.game_started = True
            cancel_embed = discord.Embed(
                title="❌ تم الإلغاء",
                description="الوقت خلص و محدش دخل, دنتو عيال فصيله",
                color=discord.Color.red()
            )
            self._refresh_panel(cancel_embed)
            self._disable_buttons()
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        start_embed = discord.Embed(
            title="🚀 بدء اللعبة",
            description="جاهزين؟؟؟",
            color=discord.Color.green()
        )
        start_embed.set_thumbnail(url=SERVER_LOGO_URL)
        self._refresh_panel(start_embed)
        self._disable_buttons()
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(run_game_loop(self.ctx, list(self.players)))
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "GeoGuessr", replace_task=self.timer_task):
            task.cancel()

    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبه شغاله يابا",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if len(self.players) >= 15:
            embed = discord.Embed(
                description="الحد الأقصى 15 لاعب!",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if interaction.user in self.players:
            embed = discord.Embed(
                description="منتا دخلت يا عم متقرفناش بقي",
                color=discord.Color.red()
            )
            await self._send_private_embed(interaction, embed)
        else:
            self.players.add(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)

    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت ياعمنااا",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        if interaction.user in self.players:
            self.players.discard(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)
        else:
            embed = discord.Embed(
                description="ولا انتا مش ف اللعبه يلا بطل استعباط",
                color=discord.Color.red()
            )
            await self._send_private_embed(interaction, embed)

    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="بس يلا هش بعيد",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="هش بعيد ياه",
                color=discord.Color.red()
            )
            return await self._send_private_embed(interaction, embed)
        self.time_left += 10
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

class GeoGuessrResultPanel(LayoutView):
    def __init__(self, title, description, outcome="neutral", image_name=None):
        super().__init__(timeout=120)
        accents = {
            "success": THEME["green"],
            "win": THEME["gold"],
            "warning": THEME["orange"],
            "neutral": THEME["grey"],
        }
        children = [TextDisplay(f"# {title}\n{description}"[:4000])]
        if image_name:
            children.extend([
                Separator(),
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{image_name}",
                        description="صورة من نتائج جولة GeoGuessr",
                    )
                ),
            ])
        self.add_item(Container(*children, accent_color=accents.get(outcome, THEME["grey"])))


class ActiveGameView(LayoutView):
    def __init__(self, executor, timer_state, round_num=1, total_rounds=6):
        super().__init__(timeout=120)
        self.executor = executor
        self.timer_state = timer_state
        self.round_num = round_num
        self.total_rounds = total_rounds
        self.message = None
        self._panel_text = TextDisplay(self._content())
        self.button = discord.ui.Button(
            label="+10 ثواني للحل",
            style=discord.ButtonStyle.secondary,
            emoji="⏱️",
            custom_id=f"geoguessr_add_time_{round_num}",
        )
        self.button.callback = self.add_time
        self.add_item(
            Container(
                self._panel_text,
                MediaGallery(
                    discord.MediaGalleryItem(
                        media="attachment://streetview.png",
                        description="صورة الجولة",
                    )
                ),
                Separator(),
                ActionRow(self.button),
                accent_color=THEME["teal"],
            )
        )

    def _content(self, remaining=None):
        if remaining is None:
            remaining = max(0, int(self.timer_state["end_time"] - time.time()))
        return (
            f"# 🌍 الجولة {self.round_num} من {self.total_rounds} — أين نحن؟\n"
            "اكتب اسم الدولة بالعربية أو الإنجليزية في الشات.\n"
            f"الوقت المتبقي: **{remaining}** ثانية.\n\n"
            f"-# كازينو الحفرة • {config.BRAND}"
        )

    def update_timer(self, remaining):
        self._panel_text.content = self._content(remaining)

    def disable_controls(self):
        self.button.disabled = True

    async def add_time(self, interaction: discord.Interaction):
        if interaction.user != self.executor:
            return await interaction.response.send_message(
                view=StatusPanel("التحكم مش متاح", "مضيف اللعبة فقط يقدر يمدد الوقت.", "warning"),
                ephemeral=True,
            )
        self.timer_state["end_time"] += 10
        remaining = max(0, int(self.timer_state["end_time"] - time.time()))
        self.update_timer(remaining)
        await interaction.response.send_message(
            view=StatusPanel("تم تمديد الوقت", "أضفنا 10 ثواني للجولة الحالية.", "success"),
            ephemeral=True,
        )
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Could not refresh GeoGuessr V2 panel after time extension", exc_info=True)


# --- COG COMMANDS ---
class GeoGuessr(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='جيو', aliases=["geo", "geoguessr", "خمن"], description="ابدأ لعبة تخمين الدولة")
    @is_staff_or_admin()
    async def start_geo(self, ctx):
        """يبدأ لعبة جيوغيسر"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = GuessGameMenu(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "GeoGuessr lobby", view=view):
            view.timer_task.cancel()
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        try:
            view.message = await ctx.send(view=view)
        except Exception:
            release_game_task(self.bot, ctx.channel.id, view.timer_task)
            view.timer_task.cancel()
            raise
        if not owns_game_task(self.bot, ctx.channel.id, view.timer_task):
            view._disable_buttons()
            await view.message.edit(view=view)

async def setup(bot):
    await bot.add_cog(GeoGuessr(bot))