import discord
from functools import partial
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task, set_game_view
import asyncio
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import io
import random
import aiohttp
import math
import os
import config

class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Developed by @neglawy", style=discord.ButtonStyle.secondary, disabled=True))

SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
from cogs.casino.permissions import is_staff_or_admin

logger = logging.getLogger(__name__)
from cogs.utility.theme import embed_to_v2, THEME, StatusPanel, lobby_embed, report_game_failure, result_embed


async def fetch_avatar(session, user, size=(80, 80)):
    try:
        async with session.get(user.display_avatar.url) as resp:
            data = await resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGBA").resize(size)
            return img
    except: 
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
        except Exception as e:
            print(f"Error loading logo: {e}")
            
    # --- SCOPE DETAILS ---
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=(255, 50, 50, 200), width=4)
    draw.line((cx, cy-radius, cx, cy+radius), fill=(255, 50, 50, 150), width=2)
    draw.line((cx-radius, cy, cx+radius, cy), fill=(255, 50, 50, 150), width=2)
    draw.ellipse((cx-4, cy-4, cx+4, cy+4), fill=(255, 0, 0, 255))
    
    # --- HUNTER AVATAR ---
    async with aiohttp.ClientSession() as session:
        try:
            avatar = await fetch_avatar(session, player, (80, 80))
            base.paste(avatar, (40, height - 120), avatar)
            draw.rectangle((40, height - 120, 120, height - 40), outline=(0, 255, 0, 200), width=3)
            
            try: font = ImageFont.truetype("tahoma.ttf", 20)
            except: font = ImageFont.load_default()
            draw.text((40, height - 35), "SNIPER ACTIVE", fill=(0, 255, 0, 200), font=font)
        except: pass
            
    out_bytes = io.BytesIO()
    base.save(out_bytes, format='PNG')
    out_bytes.seek(0)
    return out_bytes


# --- UI VIEWS ---
class KickSelect(discord.ui.Select):
    def __init__(self, chosen_player, chunk_targets, active_players, part_num, total_parts):
        self.chosen_player = chosen_player
        options = []
        for player in chunk_targets:
            real_index = active_players.index(player) + 1
            options.append(
                discord.SelectOption(
                    label=f"{real_index}- {player.display_name}",
                    value=str(player.id),
                    description="يطير بره اللعبة",
                )
            )
        placeholder = "اختار ضحية..." if total_parts == 1 else f"اختار ضحية... (قائمة {part_num})"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.chosen_player:
            return await interaction.response.send_message(
                view=StatusPanel("مش دورك", "اختيار الضحية متاح للقناص المختار فقط.", "warning"),
                ephemeral=True,
            )

        selected_id = int(self.values[0])
        if selected_id in self.view.kicked_ids:
            return await interaction.response.send_message(
                view=StatusPanel("اختيار مكرر", "أنت اخترت اللاعب ده بالفعل. اختار شخصًا آخر.", "warning"),
                ephemeral=True,
            )

        self.view.kicked_ids.append(selected_id)
        if self.view.double_shot_active and len(self.view.kicked_ids) < 2:
            return await interaction.response.send_message(
                view=StatusPanel("تم تسجيل الضحية الأولى", "اختار ضحية ثانية بسرعة.", "success"),
                ephemeral=True,
            )

        self.view._panel_text.content = (
            "# تم تسجيل الاختيار\nالاختيار اتسجل. بنجهز نتيجة الجولة دلوقتي."
        )
        await interaction.response.send_message(
            view=StatusPanel("تم اعتماد الاختيار", "استنى نتيجة الجولة.", "success"),
            ephemeral=True,
        )
        self.view.stop()


class KickView(LayoutView):
    def __init__(self, chosen_player, active_players, has_weapon, currency_cog, round_num=1):
        super().__init__(timeout=20.0)
        self.kicked_ids = []
        self.chosen_player = chosen_player
        self.double_shot_active = False
        self.currency_cog = currency_cog
        self.message = None
        self.round_num = round_num
        self._panel_text = TextDisplay(
            f"# 🎯 الروليت — الجولة {round_num}\n"
            f"القناص المختار: {chosen_player.mention}\n"
            "قدامك 20 ثانية تختار ضحية من القائمة. الاختيار النهائي بيظهر بعد اعتمادك."
        )
        components = [
            self._panel_text,
            MediaGallery(
                discord.MediaGalleryItem(
                    media="attachment://sniper.png",
                    description="صورة القناص المختار",
                )
            ),
            Separator(),
        ]
        self._action_rows = []
        self.weapon_btn = None
        if has_weapon:
            self.weapon_btn = discord.ui.Button(
                label="سلاح الحفرة",
                style=discord.ButtonStyle.secondary,
                emoji="🔫",
                custom_id="roulette_use_weapon",
            )
            self.weapon_btn.callback = self.weapon_callback
            self._action_rows.append(ActionRow(self.weapon_btn))

        targets = [player for player in active_players if player != chosen_player]
        chunk_size = 25
        total_parts = (len(targets) - 1) // chunk_size + 1
        self._selects = []
        for index in range(0, len(targets), chunk_size):
            select = KickSelect(
                chosen_player,
                targets[index:index + chunk_size],
                active_players,
                (index // chunk_size) + 1,
                total_parts,
            )
            self._selects.append(select)
            self._action_rows.append(ActionRow(select))

        self.add_item(Container(*components, *self._action_rows, accent_color=THEME["red_dark"]))

    def disable_controls(self):
        if self.weapon_btn is not None:
            self.weapon_btn.disabled = True
        for select in self._selects:
            select.disabled = True

    async def weapon_callback(self, interaction: discord.Interaction):
        if interaction.user != self.chosen_player:
            return await interaction.response.send_message(
                view=StatusPanel("السلاح مش بتاعك", "استخدام السلاح متاح للقناص المختار فقط.", "warning"),
                ephemeral=True,
            )

        success = self.currency_cog is not None and self.currency_cog.remove_item(
            self.chosen_player.id, "double_shot", 1
        )
        if not success:
            return await interaction.response.send_message(
                view=StatusPanel("تعذر استخدام السلاح", "الأداة مش موجودة في شنطتك أو نظام الاقتصاد مش متاح.", "warning"),
                ephemeral=True,
            )

        self.double_shot_active = True
        self.weapon_btn.disabled = True
        self._panel_text.content = (
            f"# الجولة {self.round_num} — السلاح اتفعل\n"
            f"{self.chosen_player.mention} فعل سلاح الحفرة. يقدر يختار ضحيتين بدل واحدة."
        )
        await interaction.response.edit_message(view=self)


class RouletteLobby(LayoutView):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 35
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام (Join)", "roulette_join", "join_btn", None),
            ("مغادرة (Leave)", "roulette_leave", "leave_btn", None),
            ("ابدأ الآن", "roulette_start", "start_btn", None),
            ("+10 ثواني", "roulette_add_time", "add_time_btn", "⏱️"),
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
            Container(
                self._panel_text,
                Separator(),
                ActionRow(*self._buttons),
                accent_color=THEME["teal"],
            )
        )
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        rules = (
            "**طريقة اللعب:**\n"
            "1- ستبدأ الجولة وسيتم اختيار قناص عشوائي.\n"
            "2- إذا كنت القناص المختار، ستختار لاعبًا من القائمة ليتم طرده.\n"
            "3- الفائز النهائي سيحصل على مكافأة **20 عملة**! 💰"
        )
        embed = lobby_embed(
            "🎯 روليت الحفرة",
            rules,
            self.players,
            self.time_left,
            max_players=45,
        )
        embed.color = discord.Color.dark_purple()
        return embed

    def _panel_content(self):
        embed = self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        return (title + f"{embed.description or ''}\n\n-# كازينو الحفرة • {config.BRAND}")[:4000]

    def _refresh_panel(self, embed=None):
        embed = embed or self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        self._panel_text.content = (title + f"{embed.description or ''}\n\n-# كازينو الحفرة • {config.BRAND}")[:4000]

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
                    logger.debug("Could not refresh roulette lobby", exc_info=True)

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) < 2:
            self.game_started = True
            embed = discord.Embed(
                description="❌ العدد غير كافي لبدء اللعبة. (تحتاج لاعبين على الأقل)",
                color=discord.Color.red()
            )
            self._panel_text.content = "# تم إلغاء اللوبي\nمحتاجين لاعبين على الأقل عشان الروليت تبدأ."
            self._disable_buttons()
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        self._panel_text.content = "# جاري تجهيز الأسلحة\nاللوبي اتقفل. الجولة هتبدأ دلوقتي."
        self._disable_buttons()
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(self.run_game_loop())
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "Roulette", replace_task=self.timer_task):
            task.cancel()

    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if len(self.players) >= 45:
            embed = discord.Embed(
                description="اكتمل العدد (45 لاعب)!",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if interaction.user in self.players:
            embed = discord.Embed(
                description="أنت مسجل بالفعل!",
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
                description="أنت لست في اللعبة!",
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


    # --- ROULETTE GAME LOOP ---
    async def run_game_loop(self):
        try:
            active_players = self.players.copy()
            round_num = 1
            currency_cog = self.ctx.bot.get_cog('Currency')

            while len(active_players) > 1:
                chosen_player = random.choice(active_players)
                
                img_bytes = await generate_sniper_image(chosen_player)
                file = discord.File(fp=img_bytes, filename="sniper.png")

                has_weapon = bool(
                    currency_cog
                    and currency_cog.get_item_count(chosen_player.id, "double_shot") > 0
                )
                view = KickView(
                    chosen_player,
                    active_players,
                    has_weapon,
                    currency_cog,
                    round_num=round_num,
                )
                msg = await self.ctx.send(view=view, file=file)
                view.message = msg
                set_game_view(self.ctx.bot, self.ctx.channel.id, asyncio.current_task(), view)
                await view.wait()

                view.disable_controls()
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    logger.debug("Could not disable roulette V2 targeting controls", exc_info=True)

                kicked_players = []
                round_notes = []
                attacker_died = False
                
                if view.kicked_ids:
                    for kid in view.kicked_ids:
                        p = next((x for x in active_players if x.id == kid), None)
                        if p:
                            # --- SHIELD RESOLUTION ---
                            if currency_cog and currency_cog.remove_item(p.id, "shield", 1):
                                round_notes.append(
                                    f"🛡️ {p.mention} فعّل الدرع، فانعكست الرصاصة على القناص {chosen_player.mention}."
                                )
                                if not attacker_died:
                                    kicked_players.append(chosen_player)
                                    attacker_died = True
                            else:
                                # --- INSURANCE RESOLUTION ---
                                if currency_cog and currency_cog.remove_item(p.id, "roulette_ins", 1):
                                    round_notes.append(
                                        f"💉 {p.mention} استخدم تأمين الروليت ونجا من الرصاصة."
                                    )
                                    continue
                                kicked_players.append(p)
                                
                # --- DUPLICATE ELIMINATION GUARD ---
                kicked_players = list(set(kicked_players))
                
                if kicked_players:
                    mentions = " و ".join([p.mention for p in kicked_players])
                    round_notes.append(f"💀 تم طرد {mentions} برا الحفرة.")
                    for p in kicked_players:
                        if p in active_players:
                            active_players.remove(p)
                elif not view.kicked_ids:
                    if chosen_player in active_players:
                        active_players.remove(chosen_player)
                    round_notes.append(f"⏰ تم طرد {chosen_player.mention} بسبب انتهاء الوقت من غير اختيار.")
                else:
                    round_notes.append("🛡️ كل اللاعبين المستهدفين نجوا بالتأمين. مفيش حد اتطرد في الجولة دي.")
                view._panel_text.content = (
                    f"# نتيجة الجولة {round_num}\n"
                    + ("\n\n".join(round_notes) if round_notes else "مفيش أحداث إضافية في الجولة دي.")
                )[:4000]
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    logger.debug("Could not update roulette V2 round summary", exc_info=True)

                round_num += 1
                await asyncio.sleep(2)

            # --- WINNER RESOLUTION ---
            if len(active_players) == 1:
                winner = active_players[0]
                final_description = f"الناجي الوحيد والأقوى في الحفرة!\n\nالفائز النهائي هو {winner.mention}."
                if currency_cog:
                    credited, _balance = currency_cog.credit_balance(winner.id, 20)
                    if credited:
                        final_description += "\n\n💰 تم إضافة 20 عملة كمكافأة فوز."
                    else:
                        final_description += "\n\n⚠️ الفوز مؤكد، لكن المكافأة لم تُصرف لأن الرصيد وصل للحد الأقصى."
                await msg.edit(
                    view=StatusPanel("🏆 بطل الروليت!", final_description, "success"),
                    attachments=[],
                )
            else:
                await msg.edit(
                    view=StatusPanel("انتهت الروليت", "الكل خرج من اللعبة؛ مفيش فائز في الجولة دي.", "warning"),
                    attachments=[],
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Roulette game loop failed")
            await report_game_failure(
                self.ctx,
                "توقفت الروليت بسبب خطأ",
                "حصل خطأ تقني أثناء الجولة. سجّلنا التفاصيل، واتقفلت اللوحة الحالية بدل تكرار رسائل جديدة.",
                fallback_view=self,
            )
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Roulette(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='روليت', description='بدء لعبة الروليت الروسي')
    @is_staff_or_admin()
    async def start_roulette(self, ctx):
        """يبدأ لعبة الروليت"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = RouletteLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Roulette lobby", view=view):
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
    await bot.add_cog(Roulette(bot))