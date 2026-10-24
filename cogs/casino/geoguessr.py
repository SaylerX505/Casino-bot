import asyncio
import logging
import os
import random
import time

import discord
from discord.ext import commands
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

from cogs.casino import geoguessr_storage as geo_storage
from cogs.casino.geoguessr_art import generate_dashboard_image, generate_result_map
from cogs.casino.game_assets import SERVER_LOGO_URL
from cogs.casino.geoguessr_views import ActiveGameView, GeoGuessrResultPanel, GuessGameMenu
from cogs.casino.game_registry import (
    is_active_game,
    owns_game_task,
    register_game_task,
    release_game_task,
    set_game_view,
)
from cogs.casino.permissions import is_staff_or_admin
from cogs.utility.theme import (
    StatusPanel,
    report_game_failure,
)

logger = logging.getLogger(__name__)


class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

DATABASE_FILE = "data/database.json"
USED_TRACKER_FILE = "data/used_images.json"
IMAGES_DIR = "images"

geolocator = Nominatim(user_agent="discord_geoguess_cogs_bot")

def load_database():
    return geo_storage.load_database(DATABASE_FILE)


def load_used_images():
    return geo_storage.load_used_images(USED_TRACKER_FILE)


def save_used_images(used_set):
    return geo_storage.save_used_images(used_set, USED_TRACKER_FILE)


game_db = load_database()
GEO_ROUNDS_PER_GAME = 6


def valid_geo_round_keys():
    return geo_storage.valid_round_keys(game_db, IMAGES_DIR)


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
    except Exception:
        logger.debug("Optional GeoGuessr enhancement failed", exc_info=True)
    return None, None

# --- CORE GAME LOOP ---
async def run_game_loop(ctx, players):
    try:
        scores = {player: 0 for player in players}
        total_rounds = GEO_ROUNDS_PER_GAME

        used_images = load_used_images()
        available_keys = set(valid_geo_round_keys())
        if len(available_keys) < total_rounds:
            return await ctx.send(
                view=StatusPanel(
                    "صور غير كافية",
                    "محتاجين 6 صور صالحة بملفات موجودة عشان نبدأ GeoGuessr.",
                    "warning",
                )
            )

        used_images.intersection_update(available_keys)
        unused_keys = list(available_keys - used_images)
        if len(unused_keys) < total_rounds:
            await ctx.send(
                view=StatusPanel(
                    "إعادة تعيين الصور",
                    "اتلعبت كل الصور الصالحة؛ هنبدأ دورة جديدة.",
                    "neutral",
                )
            )
            used_images = set()
            unused_keys = list(available_keys)

        selected_rounds = random.sample(unused_keys, total_rounds)

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

            try:
                file = discord.File(fp=image_file_path, filename="streetview.png")
            except OSError:
                logger.exception("GeoGuessr image became unavailable during a game path=%s", image_file_path)
                await round_msg.edit(
                    view=StatusPanel(
                        "تعذر تجهيز الجولة",
                        "صورة الجولة اختفت أو تعذر فتحها. أوقفنا اللعبة بدل ما نعرض جولة ناقصة.",
                        "warning",
                    ),
                    attachments=[],
                )
                return

            used_images.add(db_key)
            try:
                save_used_images(used_images)
            except OSError:
                logger.exception("Could not save GeoGuessr used-image tracker")
                await round_msg.edit(
                    view=StatusPanel(
                        "تعذر حفظ الجولة",
                        "حالة الصور لم تُحفظ بأمان، لذلك أوقفنا اللعبة قبل بدء الجولة.",
                        "warning",
                    ),
                    attachments=[],
                )
                return

            timer_state = {"end_time": time.time() + 30.0}
            active_view = ActiveGameView(ctx.author, timer_state, round_num, total_rounds)
            msg = round_msg
            try:
                await msg.edit(view=active_view, attachments=[file])
            except discord.HTTPException:
                logger.exception("Could not publish GeoGuessr round=%s", round_num)
                file.close()
                await report_game_failure(
                    ctx,
                    "تعذر عرض جولة GeoGuessr",
                    "Discord رفض نشر صورة الجولة. أوقفنا اللعبة بدل ترك جلسة غير قابلة للعب.",
                )
                return
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


# --- COG COMMANDS ---
class GeoGuessr(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='جيو', aliases=["geo", "geoguessr", "خمن"], description="ابدأ لعبة تخمين الدولة")
    @is_staff_or_admin()
    async def start_geo(self, ctx):
        """يبدأ لعبة جيوغيسر"""
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        if len(valid_geo_round_keys()) < GEO_ROUNDS_PER_GAME:
            return await ctx.send(
                view=StatusPanel(
                    "GeoGuessr غير متاحة مؤقتًا",
                    "قاعدة بيانات اللعبة أو الصور ناقصة/غير صالحة. اللعبة اتقفلت بدل ما تبدأ بجولات ناقصة.",
                    "warning",
                ),
                **send_kwargs,
            )
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send(
                view=StatusPanel("اللعبة مشغولة", "فيه لعبة أو لوبي شغال في الروم بالفعل.", "warning"),
                **send_kwargs,
            )
        view = GuessGameMenu(ctx, start_game=run_game_loop)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "GeoGuessr lobby", view=view):
            view.timer_task.cancel()
            return await ctx.send(
                view=StatusPanel("اللعبة مشغولة", "فيه لعبة أو لوبي شغال في الروم بالفعل.", "warning"),
                **send_kwargs,
            )
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