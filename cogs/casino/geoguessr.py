import discord
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task, set_game_view
import asyncio
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import io
import time
import aiohttp
import json
import random
import os
import math
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import config

from cogs.casino.permissions import is_staff_or_admin
from cogs.utility.theme import lobby_embed, report_game_failure, result_embed

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
    os.makedirs("data", exist_ok=True)
    try:
        with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def load_used_images():
    os.makedirs("data", exist_ok=True)
    try:
        with open(USED_TRACKER_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_used_images(used_set):
    os.makedirs("data", exist_ok=True)
    with open(USED_TRACKER_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(used_set), f)

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
    except: pass
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
            except: pass

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
    except: pass

    draw = ImageDraw.Draw(board)
    try:
        font_large = ImageFont.truetype("arial.ttf", 40)
        font_medium = ImageFont.truetype("arial.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except:
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
            except: pass

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
            embed = discord.Embed(
                description="🔄 **تنبيه:** تم لعب جميع الصور! جاري إعادة تعيين الصور...",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            used_images = set()
            unused_keys = list(available_keys)

        if len(unused_keys) < total_rounds:
            embed = discord.Embed(
                description="🚨 قاعدة البيانات لا تحتوي على صور كافية!",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        selected_rounds = random.sample(unused_keys, total_rounds)
        used_images.update(selected_rounds)
        save_used_images(used_images)

        for round_num, db_key in enumerate(selected_rounds, 1):
            loading_embed = discord.Embed(
                title=f"🌍 الجولة {round_num} من {total_rounds}",
                description="بدور علي مكان رايق اتقل.....",
                color=discord.Color.dark_grey()
            )
            loading_embed.set_thumbnail(url=SERVER_LOGO_URL)
            round_msg = await ctx.send(embed=loading_embed)

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
                embed = discord.Embed(
                    description=f"🚨 الصورة غير موجودة في مجلد {IMAGES_DIR}. سيتم تخطي الجولة.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                continue

            game_embed = discord.Embed(
                title=f"🌍 الجولة {round_num} - أين نحن في هذا العالم؟",
                description="اكتب اسم الدولة (بالعربية أو الإنجليزية) في الشات!\nالوقت المتبقي: **30** ثواني.",
                color=discord.Color.blue()
            )
            game_embed.set_image(url="attachment://streetview.png")
            game_embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}", icon_url=SERVER_LOGO_URL)
            
            timer_state = {"end_time": time.time() + 30.0}
            active_view = ActiveGameView(ctx.author, timer_state)
            
            msg = await ctx.send(embed=game_embed, file=file, view=active_view)
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
                    game_embed.description = f"اكتب اسم الدولة (بالعربية أو الإنجليزية) في الشات!\nالوقت المتبقي: **{remaining}** ثواني."
                    try: await msg.edit(embed=game_embed)
                    except: pass
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

            for child in active_view.children:
                child.disabled = True
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

            summary_embed = discord.Embed(
                title="📊 ملخص الجولة",
                description=summary_text,
                color=discord.Color.light_grey()
            )
            summary_embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}", icon_url=SERVER_LOGO_URL)
            await ctx.send(embed=summary_embed)
            await asyncio.sleep(2) 

            map_bytes = await generate_result_map(actual_country_center_coords, player_results)
            map_file = discord.File(fp=map_bytes, filename="result_map.png")
            map_embed = discord.Embed(
                title="ادي خريطه شوفو بقي كل واحد اجابتو كانت فين..",
                color=discord.Color.dark_theme()
            )
            map_embed.set_image(url="attachment://result_map.png")
            await ctx.send(embed=map_embed, file=map_file)
            await asyncio.sleep(3)

            board_bytes = await generate_dashboard_image(player_results, round_num, total_rounds)
            board_file = discord.File(fp=board_bytes, filename="dashboard.png")
            board_embed = result_embed(
                title="🏆 لوحة النتائج",
                description="ملخص ترتيب اللاعبين بعد الجولة.",
                outcome="neutral",
            )
            board_embed.set_image(url="attachment://dashboard.png")
            await ctx.send(embed=board_embed, file=board_file)
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
            
        final_embed = result_embed(
            title="🏁 انتهت اللعبة! 🏁",
            description=desc,
            outcome="win" if winners else "neutral",
            thumbnail_url=thumb,
        )
        await ctx.send(embed=final_embed, view=WatermarkView())

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
class GuessGameMenu(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = set()
        self.game_started = False
        self.time_left = 40
        self.message = None
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

    async def run_timer(self):
        while self.time_left > 0 and not self.game_started:
            await asyncio.sleep(5)
            if self.game_started: break
            self.time_left -= 5
            if self.time_left <= 0: await self.auto_start()
            elif self.message:
                try: await self.message.edit(embed=self.get_embed())
                except: pass

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) == 0:
            self.game_started = True
            cancel_embed = discord.Embed(
                title="❌ تم الإلغاء",
                description="الوقت خلص و محدش دخل, دنتو عيال فصيله",
                color=discord.Color.red()
            )
            await self.ctx.send(embed=cancel_embed)
            for child in self.children: child.disabled = True
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        start_embed = discord.Embed(
            title="🚀 بدء اللعبة",
            description="جاهزين؟؟؟",
            color=discord.Color.green()
        )
        start_embed.set_thumbnail(url=SERVER_LOGO_URL)
        await self.ctx.send(embed=start_embed, delete_after=5)
        for child in self.children: child.disabled = True
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(run_game_loop(self.ctx, list(self.players)))
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "GeoGuessr", replace_task=self.timer_task):
            task.cancel()

    @discord.ui.button(label="انضمام (Join)", style=discord.ButtonStyle.secondary)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبه شغاله يابا",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if len(self.players) >= 15:
            embed = discord.Embed(
                description="الحد الأقصى 15 لاعب!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.user in self.players:
            embed = discord.Embed(
                description="منتا دخلت يا عم متقرفناش بقي",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            self.players.add(interaction.user)
            await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="مغادرة (Leave)", style=discord.ButtonStyle.secondary)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت ياعمنااا",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.user in self.players:
            self.players.discard(interaction.user)
            await interaction.response.edit_message(embed=self.get_embed())
        else:
            embed = discord.Embed(
                description="ولا انتا مش ف اللعبه يلا بطل استعباط",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="ابدأ الآن", style=discord.ButtonStyle.secondary)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="بس يلا هش بعيد",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    @discord.ui.button(label="+10 ثواني", style=discord.ButtonStyle.secondary, emoji="⏱️")
    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="هش بعيد ياه",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        self.time_left += 10
        await interaction.response.edit_message(embed=self.get_embed())

class ActiveGameView(discord.ui.View):
    def __init__(self, executor, timer_state):
        super().__init__(timeout=120)
        self.executor = executor
        self.timer_state = timer_state

    @discord.ui.button(label="+10 ثواني للحل", style=discord.ButtonStyle.secondary, emoji="⏱️")
    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.executor:
            embed = discord.Embed(
                description="هش يلا بقي",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        self.timer_state["end_time"] += 10
        embed = discord.Embed(
            description=f"احلي 10 ثواني عليك يا عم {interaction.user.mention}!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
            view.message = await ctx.send(embed=view.get_embed(), view=view)
        except Exception:
            release_game_task(self.bot, ctx.channel.id, view.timer_task)
            view.timer_task.cancel()
            raise
        if not owns_game_task(self.bot, ctx.channel.id, view.timer_task):
            for child in view.children:
                child.disabled = True
            await view.message.edit(view=view)

async def setup(bot):
    await bot.add_cog(GeoGuessr(bot))