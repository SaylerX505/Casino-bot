import discord
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task, set_game_view
import asyncio
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
from cogs.casino.permissions import is_staff_or_owner
from cogs.utility.theme import lobby_embed, result_embed


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
        for p in chunk_targets:
            real_index = active_players.index(p) + 1
            options.append(discord.SelectOption(label=f"{real_index}- {p.display_name}", value=str(p.id), description="يطير بره اللعبة"))
            
        placeholder = "اختار ضحية..." if total_parts == 1 else f"اختار ضحية... (قائمة {part_num})"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.chosen_player:
            embed = discord.Embed(
                description="مش دورك يا نجم!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        selected_id = int(self.values[0])
        
        if selected_id in self.view.kicked_ids:
            embed = discord.Embed(
                description="انت اخترت الشخص ده خلاص! شوف حد تاني.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        self.view.kicked_ids.append(selected_id)

        if self.view.double_shot_active and len(self.view.kicked_ids) < 2:
            embed = discord.Embed(
                description="✅ تم تحديد الضحية الأولى! اختار الشخص التاني بسرعة.",
                color=discord.Color.green()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.send_message("✅ تم اعتماد اختيارك.", ephemeral=True)
        self.view.stop()

class KickView(discord.ui.View):
    def __init__(self, chosen_player, active_players, has_weapon, currency_cog):
        super().__init__(timeout=20.0)
        self.kicked_ids = []
        self.chosen_player = chosen_player
        self.double_shot_active = False
        self.currency_cog = currency_cog
        
        if has_weapon:
            self.weapon_btn = discord.ui.Button(label="سلاح الحفرة", style=discord.ButtonStyle.secondary, emoji="🔫", custom_id="use_weapon")
            self.weapon_btn.callback = self.weapon_callback
            self.add_item(self.weapon_btn)
        
        targets = [p for p in active_players if p != chosen_player]
        chunk_size = 25
        total_parts = (len(targets) - 1) // chunk_size + 1
        
        for i in range(0, len(targets), chunk_size):
            chunk = targets[i:i + chunk_size]
            self.add_item(KickSelect(chosen_player, chunk, active_players, (i//chunk_size)+1, total_parts))

    async def weapon_callback(self, interaction: discord.Interaction):
        if interaction.user != self.chosen_player:
            embed = discord.Embed(
                description="السلاح ده مش بتاعك!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        success = self.currency_cog.remove_item(self.chosen_player.id, "double_shot", 1)
        if not success:
            embed = discord.Embed(
                description="❌ حصل مشكلة في استخدام السلاح!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        self.double_shot_active = True
        self.weapon_btn.disabled = True
        
        embed = discord.Embed(
            description=f"🔥 **{self.chosen_player.mention} فعّل سلاح الحفرة!** يقدر يطرد شخصين الجولة دي! اختار ضحيتين بسرعة:",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=self)


class RouletteLobby(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 35
        self.message = None
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
        if len(self.players) < 2:
            self.game_started = True
            embed = discord.Embed(
                description="❌ العدد غير كافي لبدء اللعبة. (تحتاج لاعبين على الأقل)",
                color=discord.Color.red()
            )
            await self.ctx.send(embed=embed)
            for child in self.children: child.disabled = True
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        embed = discord.Embed(
            description="🚀 جاري تجهيز الأسلحة...",
            color=discord.Color.blue()
        )
        await self.ctx.send(embed=embed, delete_after=3)
        for child in self.children: child.disabled = True
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(self.run_game_loop())
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "Roulette", replace_task=self.timer_task):
            task.cancel()

    @discord.ui.button(label="انضمام (Join)", style=discord.ButtonStyle.secondary)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if len(self.players) >= 45:
            embed = discord.Embed(
                description="اكتمل العدد (45 لاعب)!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.user in self.players:
            embed = discord.Embed(
                description="أنت مسجل بالفعل!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            self.players.append(interaction.user)
            await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="مغادرة (Leave)", style=discord.ButtonStyle.secondary)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.user in self.players:
            self.players.remove(interaction.user)
            await interaction.response.edit_message(embed=self.get_embed())
        else:
            embed = discord.Embed(
                description="أنت لست في اللعبة!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="ابدأ الآن", style=discord.ButtonStyle.secondary)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    @discord.ui.button(label="+10 ثواني", style=discord.ButtonStyle.secondary, emoji="⏱️")
    async def add_time_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        self.time_left += 10
        await interaction.response.edit_message(embed=self.get_embed())


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
                
                embed = discord.Embed(
                    title=f"🎯 الجولة {round_num}",
                    description=f"✅ القناص اتحدد!\nقدامك **20 ثانية** تختار ضحيتك...",
                    color=discord.Color.red()
                )
                embed.set_image(url="attachment://sniper.png")
                
                has_weapon = False
                if currency_cog:
                    has_weapon = currency_cog.get_item_count(chosen_player.id, "double_shot") > 0
                
                view = KickView(chosen_player, active_players, has_weapon, currency_cog)
                msg = await self.ctx.send(content=f"🔔 السلاح في إيدك يا **{chosen_player.mention}** دورك جه!", embed=embed, file=file, view=view)
                view.message = msg
                set_game_view(self.ctx.bot, self.ctx.channel.id, asyncio.current_task(), view)
                await view.wait()
                
                for child in view.children: child.disabled = True
                try: await msg.edit(view=view)
                except: pass

                kicked_players = []
                attacker_died = False
                
                if view.kicked_ids:
                    for kid in view.kicked_ids:
                        p = next((x for x in active_players if x.id == kid), None)
                        if p:
                            # --- SHIELD RESOLUTION ---
                            if currency_cog and currency_cog.get_item_count(p.id, "shield") > 0:
                                currency_cog.remove_item(p.id, "shield", 1)
                                embed = discord.Embed(
                                    title="🛡️ Shield Activation!",
                                    description=f"**Hold on!** {p.mention} had a **Shield (درع الحفره)** equipped!\n"
                                                f"The shield reflected the shot back at {chosen_player.mention}!",
                                    color=discord.Color.blue()
                                )
                                embed.set_thumbnail(url="https://emojipedia-us.s3.dualstack.us-west-2.amazonaws.com/thumbs/120/twitter/322/shield_1f6e1-fe0f.png")
                                await self.ctx.send(embed=embed)
                                if not attacker_died:
                                    kicked_players.append(chosen_player)
                                    attacker_died = True
                            else:
                                # --- INSURANCE RESOLUTION ---
                                if currency_cog and currency_cog.get_item_count(p.id, "roulette_ins") > 0:
                                    currency_cog.remove_item(p.id, "roulette_ins", 1)
                                    embed = discord.Embed(
                                        description=f"💉 **{p.mention}** استخدم **تأمين الروليت**! الرصاصة عدت من جنبه... عاش يا وحش!",
                                        color=discord.Color.gold()
                                    )
                                    await self.ctx.send(embed=embed)
                                    continue
                                kicked_players.append(p)
                                
                # --- DUPLICATE ELIMINATION GUARD ---
                kicked_players = list(set(kicked_players))
                
                if kicked_players:
                    mentions = " و ".join([p.mention for p in kicked_players])
                    embed = discord.Embed(
                        description=f"💀 تم طرد {mentions} برا الحفرة!",
                        color=discord.Color.red()
                    )
                    await self.ctx.send(embed=embed)
                    for p in kicked_players:
                        if p in active_players:
                            active_players.remove(p)
                elif not view.kicked_ids:
                    if chosen_player in active_players:
                        active_players.remove(chosen_player)
                    embed = discord.Embed(
                        description=f"⏰ تم طرد {chosen_player.mention} برا الحفرة بسبب الخمول!",
                        color=discord.Color.red()
                    )
                    await self.ctx.send(embed=embed)
                else:
                    embed = discord.Embed(
                        description="🛡️ كل اللاعبين المستهدفين نجوا بالتأمين. مفيش حد اتطرد في الجولة دي.",
                        color=discord.Color.gold()
                    )
                    await self.ctx.send(embed=embed)
                
                round_num += 1
                await asyncio.sleep(4)

            # --- WINNER RESOLUTION ---
            if len(active_players) == 1:
                winner = active_players[0]
                final_embed = result_embed(
                    title="🏆 بطل الروليت!",
                    description=f"الناجي الوحيد والأقوى في الحفرة!\n\n🎉 الفائز النهائي هو **{winner.mention}**!",
                    outcome="win",
                    thumbnail_url=winner.display_avatar.url,
                )
                
                if currency_cog:
                    currency_cog.update_balance(winner.id, 20)
                    final_embed.description += f"\n\n💰 تم إضافة **20 عملة** لحسابك كمكافأة فوز!"
                    
                await self.ctx.send(embed=final_embed, view=WatermarkView())
            else:
                embed = result_embed(
                    title="انتهت الروليت",
                    description="💀 الكل طار! مفيش فايز الجيم ده.",
                    outcome="loss",
                )
                await self.ctx.send(embed=embed, view=WatermarkView())

        except asyncio.CancelledError:
            raise
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Roulette(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='روليت', description='بدء لعبة الروليت الروسي')
    @is_staff_or_owner()
    async def start_roulette(self, ctx):
        """يبدأ لعبة الروليت"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = RouletteLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Roulette lobby", view=view):
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
    await bot.add_cog(Roulette(bot))