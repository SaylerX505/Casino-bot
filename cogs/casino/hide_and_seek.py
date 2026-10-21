import discord
from functools import partial
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task, set_game_view
import asyncio
import logging
from PIL import Image, ImageDraw
import io
import random
import config

from cogs.casino.permissions import is_staff_or_admin
from cogs.utility.theme import THEME, StatusPanel, lobby_embed, report_game_failure, result_embed


logger = logging.getLogger(__name__)


class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Developed by @neglawy", style=discord.ButtonStyle.secondary, disabled=True))

SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"

# --- LOCATION COORDINATES ---
LOCATIONS = {
    1: {"name": "الحفره", "pos": (0.85, 0.20)},
    2: {"name": "محطة الوقود", "pos": (0.65, 0.25)},
    3: {"name": "المدرسة", "pos": (0.35, 0.15)},
    4: {"name": "المستودع", "pos": (0.15, 0.40)},
    5: {"name": "محطة الحافلات", "pos": (0.45, 0.75)},
    6: {"name": "المصنع", "pos": (0.40, 0.60)},
    7: {"name": "المرآب", "pos": (0.15, 0.25)},
    8: {"name": "ملعب الأطفال", "pos": (0.75, 0.75)},
    9: {"name": "الحديقة", "pos": (0.40, 0.40)},
    10: {"name": "النفق", "pos": (0.05, 0.75)},
    11: {"name": "الكابينة", "pos": (0.10, 0.85)},
    12: {"name": "المستشفى", "pos": (0.60, 0.45)},
    13: {"name": "الجسر", "pos": (0.60, 0.80)},
    14: {"name": "السوق", "pos": (0.40, 0.25)},
    15: {"name": "العمارة", "pos": (0.65, 0.60)},
    16: {"name": "المقهى", "pos": (0.50, 0.90)},
    17: {"name": "موقع البناء", "pos": (0.10, 0.55)},
    18: {"name": "المزرعة", "pos": (0.60, 0.15)},
    19: {"name": "الملعب", "pos": (0.25, 0.70)},
    20: {"name": "الميناء", "pos": (0.30, 0.90)},
    21: {"name": "خزان المياه", "pos": (0.10, 0.15)},
    22: {"name": "الكهف", "pos": (0.75, 0.90)},
    23: {"name": "مركز الشرطة", "pos": (0.85, 0.50)},
    24: {"name": "برج الإتصالات", "pos": (0.90, 0.35)},
    25: {"name": "المنارة", "pos": (0.95, 0.80)}
}

async def generate_map_with_x(searched_locations):
    try:
        base = Image.open("mafiagame/locations.png").convert("RGBA")
    except Exception:
        base = Image.new("RGBA", (1200, 800), (40, 40, 40, 255))
        
    draw = ImageDraw.Draw(base)
    width, height = base.size
    
    for loc_id in searched_locations:
        if loc_id in LOCATIONS:
            pos_pct = LOCATIONS[loc_id]["pos"]
            cx = int(width * pos_pct[0])
            cy = int(height * pos_pct[1])
            
            size = 35
            draw.line((cx-size, cy-size, cx+size, cy+size), fill=(255, 20, 20, 255), width=10)
            draw.line((cx+size, cy-size, cx-size, cy+size), fill=(255, 20, 20, 255), width=10)
            
    out = io.BytesIO()
    base.save(out, format="PNG")
    out.seek(0)
    return out


class HideDropdown(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=f"{k}- {v['name']}", value=str(k)) for k, v in LOCATIONS.items()]
        super().__init__(placeholder="اختار مكان تستخبى فيه بسرعة...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user not in self.view.players:
            embed = discord.Embed(
                description="انت مش في اللعبة يا كابتن!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if interaction.user in self.view.hiding_spots:
            embed = discord.Embed(
                description="انت استخبيت خلاص! خليك مكانك ومتتحركش.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        chosen_spot = int(self.values[0])
        self.view.hiding_spots[interaction.user] = chosen_spot
        embed = discord.Embed(
            description=f"✅ تمام! انت دلوقتي مستخبي في **{LOCATIONS[chosen_spot]['name']}**. خليك ساكت!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if len(self.view.hiding_spots) == len(self.view.players):
            self.view.stop()

class HidingView(discord.ui.View):
    def __init__(self, players):
        super().__init__(timeout=50.0) 
        self.players = players
        self.hiding_spots = {}
        self.add_item(HideDropdown())


class SeekDropdown(discord.ui.Select):
    def __init__(self, seeker, searched_locations):
        self.seeker = seeker
        options = []
        for loc_id, loc_data in LOCATIONS.items():
            if loc_id not in searched_locations:
                options.append(discord.SelectOption(label=f"{loc_id}- {loc_data['name']}", value=str(loc_id)))
                
        super().__init__(placeholder="اختار مكان تفتشه...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.seeker:
            embed = discord.Embed(
                description="مش دورك تدور!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        self.view.choice = int(self.values[0])
        await interaction.response.send_message("✅ تم تسجيل اختيارك.", ephemeral=True)
        self.view.stop()

class SeekerView(discord.ui.View):
    def __init__(self, seeker, searched_locations):
        super().__init__(timeout=15.0)
        self.seeker = seeker
        self.choice = None
        self.add_item(SeekDropdown(seeker, searched_locations))


class LobbyView(LayoutView):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 30
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("استخبى (Join)", "hide_seek_join", "join_btn", None),
            ("مغادرة (Leave)", "hide_seek_leave", "leave_btn", None),
            ("ابدأ الآن", "hide_seek_start", "start_btn", None),
            ("+10 ثواني", "hide_seek_add_time", "add_time_btn", "⏱️"),
        ):
            button = discord.ui.Button(
                label=label,
                custom_id=custom_id,
                style=discord.ButtonStyle.secondary,
                emoji=emoji,
            )
            button.callback = partial(getattr(self, callback_name), button=button)
            self._buttons.append(button)
        self.add_item(
            Container(self._panel_text, Separator(), ActionRow(*self._buttons), accent_color=THEME["grey"])
        )
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        embed = lobby_embed(
            "🙈 غميضة الحفرة",
            "الخريطة كبيرة واللي هيتمسك هيتطرد بره الحفرة.",
            self.players,
            self.time_left,
            max_players=25,
        )
        embed.color = discord.Color.dark_gray()
        embed.set_thumbnail(url=SERVER_LOGO_URL)
        return embed

    def _panel_content(self):
        embed = self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        description = f"{embed.description or ''}\n\n"
        return (title + description + f"-# كازينو الحفرة • {config.BRAND}")[:4000]

    def _refresh_panel(self):
        self._panel_text.content = self._panel_content()

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _private_notice(self, interaction, title, description):
        await interaction.response.send_message(
            view=StatusPanel(title, description, "warning"),
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
                    logger.debug("Could not refresh hide-and-seek lobby", exc_info=True)

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) < 2:
            self.game_started = True
            embed = discord.Embed(
                description="❌ العدد غير كافي لبدء الغميضة. (تحتاج لاعبين على الأقل)",
                color=discord.Color.red()
            )
            self._panel_text.content = "# تم إلغاء اللوبي\nمحتاجين لاعبين على الأقل لبدء الغميضة."
            self._disable_buttons()
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        self._panel_text.content = "# جاري بدء الغميضة\nاللوبي اتقفل. جهّز نفسك."
        self._disable_buttons()
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(run_game_loop(self.ctx, self.players))
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "HideAndSeek", replace_task=self.timer_task):
            task.cancel()

    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت خلاص!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if len(self.players) >= 25:
            embed = discord.Embed(
                description="الخريطة اتملت (25 لاعب)!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if interaction.user in self.players:
            embed = discord.Embed(
                description="أنت مسجل اسمك!",
                color=discord.Color.red()
            )
            await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        else:
            self.players.append(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)

    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if interaction.user in self.players:
            self.players.remove(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)
        else:
            embed = discord.Embed(
                description="أنت مش في اللعبة!",
                color=discord.Color.red()
            )
            await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")

    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    async def add_time_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        self.time_left += 10
        self._refresh_panel()
        await interaction.response.edit_message(view=self)


# --- GAME LOOP ---
async def run_game_loop(ctx, players):
    try:
        map_bytes = await generate_map_with_x([])
        hide_embed = discord.Embed(
            title="🏃‍♂️ وقت الغميضة!",
            description="الكل يغمض عينه! قدامكم **50 ثانية** تختاروا مكان تستخبوا فيه من القائمة اللي تحت.\nاللي مش هيستخبى هيتطرد!",
            color=discord.Color.gold()
        )
        hide_embed.set_image(url="attachment://map.png")
        
        hide_view = HidingView(players)
        hide_msg = await ctx.send(embed=hide_embed, file=discord.File(fp=map_bytes, filename="map.png"), view=hide_view)
        hide_view.message = hide_msg
        set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), hide_view)
        await hide_view.wait()
        
        for c in hide_view.children: c.disabled = True
        try:
            await hide_msg.edit(view=hide_view)
        except discord.HTTPException:
            logger.debug("Could not disable hide-and-seek hiding controls", exc_info=True)

        hiding_spots = hide_view.hiding_spots
        alive = []
        afk_players = []
        
        for p in players:
            if p in hiding_spots:
                alive.append(p)
            else:
                afk_players.append(p)
                
        if afk_players:
            afk_text = "، ".join([p.mention for p in afk_players])
            embed = discord.Embed(
                description=f"⚠️ **النظام:** {afk_text} معرفوش يستخبوا في الوقت المحدد وانكشفوا! تم استبعادهم.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

        if len(alive) < 2:
            embed = discord.Embed(
                title="❌ انتهت اللعبة",
                description="مبقاش في عدد كافي عشان نكمل الغميضة!",
                color=discord.Color.dark_grey()
            )
            await ctx.send(embed=embed, view=WatermarkView())
            return

        embed = discord.Embed(
            description="🤫 **خلاص! الكل استخبى... هنبدأ التدوير!**",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        await asyncio.sleep(3)

        searched_locations = set()
        last_seeker = None 

        while len(alive) > 1:
            if len(alive) > 1 and last_seeker in alive:
                available_seekers = [p for p in alive if p != last_seeker]
                seeker = random.choice(available_seekers)
            else:
                seeker = random.choice(alive)
                
            last_seeker = seeker
            
            seek_embed = discord.Embed(
                title="🔎 وقت التفتيش",
                description=f"القرعة وقعت على {seeker.mention}! 🔦\nقدامك **15 ثانية** تختار مكان تفتشه من القائمة اللي تحت.",
                color=discord.Color.blue()
            )
            
            seek_view = SeekerView(seeker, searched_locations)
            seek_msg = await ctx.send(content=f"🔔 دورك جه يا {seeker.mention}!", embed=seek_embed, view=seek_view)
            seek_view.message = seek_msg
            set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), seek_view)
            await seek_view.wait()
            
            for c in seek_view.children: c.disabled = True
            try:
                await seek_msg.edit(view=seek_view)
            except discord.HTTPException:
                logger.debug("Could not disable hide-and-seek seeking controls", exc_info=True)

            chosen_spot = seek_view.choice
            
            if chosen_spot is None:
                alive.remove(seeker)
                embed = discord.Embed(
                    description=f"⏰ تم طرد {seeker.display_name} بسبب الخمول.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                
            else:
                spot_name = LOCATIONS[chosen_spot]['name']
                
                if hiding_spots[seeker] == chosen_spot:
                    alive.remove(seeker)
                    embed = discord.Embed(
                        description=f"🤡 **يا للغباء!** {seeker.mention} راح يفتش ونسى إنه مستخبي هناك أصلاً! فضح نفسه وتم استبعاده، لكن المكان لسه متغطى للناس التانية.",
                        color=discord.Color.dark_red()
                    )
                    await ctx.send(embed=embed)
                    
                else:
                    searched_locations.add(chosen_spot)
                    
                    found_players = [p for p in alive if hiding_spots[p] == chosen_spot]
                            
                    if found_players:
                        mentions = " و ".join([p.mention for p in found_players])
                        for p in found_players:
                            alive.remove(p)
                        
                        if len(found_players) > 1:
                            embed = discord.Embed(
                                description=f"🚨 **مـجـزرة!** {seeker.mention} فتش في **{spot_name}** ولقى {mentions} مستخبيين مع بعض هناك! كلهم طاروا بره الحفرة.",
                                color=discord.Color.green()
                            )
                            await ctx.send(embed=embed)
                        else:
                            embed = discord.Embed(
                                description=f"🚨 **قـفـشـتـك!** {seeker.mention} فتش في **{spot_name}** ولقى {mentions} مستخبي هناك! طار بره الحفرة.",
                                color=discord.Color.green()
                            )
                            await ctx.send(embed=embed)
                    else:
                        embed = discord.Embed(
                            description=f"💨 {seeker.mention} فتش في **{spot_name}** وملقاش غير التراب... المكان فاضي!",
                            color=discord.Color.light_grey()
                        )
                        await ctx.send(embed=embed)

            await asyncio.sleep(2)
            
            updated_map = await generate_map_with_x(searched_locations)
            await ctx.send(file=discord.File(fp=updated_map, filename="map_update.png"))
            await asyncio.sleep(3)

        # --- WINNER ANNOUNCEMENT ---
        if len(alive) == 1:
            winner = alive[0]
            win_embed = result_embed(
                title="🏆 بطل الغميضة!",
                description=f"الكل اتكشف ومتبقاش غير شبح الحفرة!\n\n🎉 ألف مبروك لـ **{winner.mention}** فاز بالجيم!",
                outcome="win",
                thumbnail_url=winner.display_avatar.url,
            )
            await ctx.send(embed=win_embed, view=WatermarkView())
        else:
            embed = result_embed(
                title="انتهت الغميضة",
                description="💀 الكل اتكشف! مفيش فايز النهاردة.",
                outcome="loss",
            )
            await ctx.send(embed=embed, view=WatermarkView())

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Hide-and-seek game loop failed")
        await report_game_failure(
            ctx,
            "توقفت الغميضة بسبب خطأ",
            "حصل عطل غير متوقع أثناء الجولة. تم تسجيل الخطأ، ولم يتم تنفيذ أي خصم إضافي بسبب الخطأ.",
        )
    finally:
        release_game_task(ctx.bot, ctx.channel.id, asyncio.current_task())

class HideAndSeek(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='غميضه', description='بدء لعبة الغميضة')
    @is_staff_or_admin()
    async def start_hide_seek(self, ctx):
        """يبدأ لعبة الغميضة في الحفرة"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = LobbyView(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "HideAndSeek lobby", view=view):
            view.timer_task.cancel()
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        try:
            view.message = await ctx.send(view=view)
        except Exception as e:
            release_game_task(self.bot, ctx.channel.id, view.timer_task)
            view.timer_task.cancel()
            embed = discord.Embed(
                description=f"⚠️ حدث خطأ: {e}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        if not owns_game_task(self.bot, ctx.channel.id, view.timer_task):
            view._disable_buttons()
            await view.message.edit(view=view)

async def setup(bot):
    await bot.add_cog(HideAndSeek(bot))