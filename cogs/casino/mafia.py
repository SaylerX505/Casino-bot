import discord
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task, set_game_view
import asyncio
from PIL import Image, ImageDraw
import io
import random
import aiohttp
import config

from cogs.casino.permissions import is_staff_or_owner
from cogs.utility.theme import lobby_embed


class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Developed by @neglawy & @17 Nicotine", style=discord.ButtonStyle.secondary, disabled=True))

SERVER_LOGO_URL = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
AVATAR_SETTINGS = {
    "dead_civilian": {"pos": (655, 425), "size": (50, 50)},
    "dead_doctor":   {"pos": (189, 331), "size": (90, 90)},
    "saved_person":  {"pos": (190, 144), "size": (130, 130)},
    "mafia_1": [{"pos": (520, 331), "size": (180, 180)}], 
    "mafia_2": [{"pos": (250, 285), "size": (150, 150)}, {"pos": (565, 353), "size": (150, 150)}],
    "mafia_3": [{"pos": (133, 147), "size": (50, 50)}, {"pos": (316, 180), "size": (45, 45)}, {"pos": (512, 171), "size": (50, 50)}]
}

async def fetch_avatar(session, user, size):
    try:
        async with session.get(user.display_avatar.url) as resp:
            data = await resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGBA").resize(size)
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size[0], size[1]), fill=255)
            circular = Image.new("RGBA", size, (0, 0, 0, 0))
            circular.paste(img, (0, 0), mask)
            return circular
    except asyncio.CancelledError:
        raise
    except Exception:
        return Image.new("RGBA", size, (50, 50, 50, 255))

async def generate_billboard(alive_players):
    try:
        base = Image.open("mafiagame/billboard.jpg").convert("RGBA")
    except:
        base = Image.new('RGBA', (1200, 600), (30, 30, 30, 255))
    width, height = base.size
    cols, avatar_size, padding_x, padding_y, start_y = 9, 70, 25, 30, 100
    rows = [alive_players[i:i + cols] for i in range(0, len(alive_players), cols)]
    async with aiohttp.ClientSession() as session:
        for row_idx, row_players in enumerate(rows):
            items_in_row = len(row_players)
            row_width = (items_in_row * avatar_size) + ((items_in_row - 1) * padding_x)
            row_start_x = (width - row_width) // 2
            for col_idx, player in enumerate(row_players):
                x = row_start_x + (col_idx * (avatar_size + padding_x))
                y = start_y + (row_idx * (avatar_size + padding_y))
                avatar = await fetch_avatar(session, player, size=(avatar_size, avatar_size))
                base.paste(avatar, (int(x), int(y)), avatar)
    out = io.BytesIO()
    base.save(out, format='PNG')
    out.seek(0)
    return out

async def generate_action_image(base_img, target, key):
    try:
        base = Image.open(f"mafiagame/{base_img}").convert("RGBA")
    except:
        base = Image.new('RGBA', (1200, 600), (0, 0, 0, 255))
    s = AVATAR_SETTINGS[key]
    async with aiohttp.ClientSession() as session:
        avatar = await fetch_avatar(session, target, size=s["size"])
        base.paste(avatar, (int(s["pos"][0]-s["size"][0]/2), int(s["pos"][1]-s["size"][1]/2)), avatar)
    out = io.BytesIO()
    base.save(out, format='PNG')
    out.seek(0)
    return out

async def generate_mafia_reveal(mafias):
    count = len(mafias)
    try:
        base = Image.open(f"mafiagame/mafia_{count}.jpg").convert("RGBA")
    except:
        base = Image.new('RGBA', (1200, 600), (20, 0, 0, 255))
    configs = AVATAR_SETTINGS[f"mafia_{count}"]
    async with aiohttp.ClientSession() as session:
        for i, p in enumerate(mafias):
            if i >= len(configs): break
            pos, size = configs[i]["pos"], configs[i]["size"]
            avatar = await fetch_avatar(session, p, size=size)
            base.paste(avatar, (int(pos[0]-size[0]/2), int(pos[1]-size[1]/2)), avatar)
    out = io.BytesIO()
    base.save(out, format='PNG')
    out.seek(0)
    return out

async def poke_reminder(user, role_name, view):
    await asyncio.sleep(10)
    if not view.selected_id and not view.is_finished():
        try:
            if role_name == "mafia":
                await user.send("⏳ يا زعيم! الوقت بيجري والضحية هتهرب، خلصنا واختار حد نخلص عليه من القايمة اللي في السيرفر!")
            else:
                await user.send("⏳ يا دكتور! المريض بيموت في إيدك، انجز واختار هتعالج مين من القايمة اللي في السيرفر!")
        except asyncio.CancelledError:
            raise
        except discord.HTTPException:
            pass

class MafiaDropdown(discord.ui.Select):
    def __init__(self, targets, view_parent):
        self.view_parent = view_parent
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in targets[:25]]
        super().__init__(placeholder="اختار ضحيتك المرة دي...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view_parent.selected_id = int(self.values[0])
        embed = discord.Embed(
            description="🩸 الأوامر صدرت... ارجع للشات الأساسي.",
            color=discord.Color.dark_red()
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.view_parent.stop()

class EphemeralActionView(discord.ui.View):
    def __init__(self, targets):
        super().__init__(timeout=20.0) 
        self.selected_id = None
        self.add_item(MafiaDropdown(targets, self))

class SecretButtonView(discord.ui.View):
    def __init__(self, active_user, role_users, targets, role_name):
        super().__init__(timeout=20.0)
        self.active_user = active_user
        self.role_users = role_users
        self.targets = targets
        self.role_name = role_name
        self.selected_id = None
        
        btn_label = "🔪 افتح قايمة الاغتيال" if role_name == "mafia" else "💉 افتح شنطة الإسعافات"
        btn_style = discord.ButtonStyle.danger if role_name == "mafia" else discord.ButtonStyle.success
        
        btn = discord.ui.Button(label=btn_label, style=btn_style)
        btn.callback = self.btn_callback
        self.add_item(btn)

    async def btn_callback(self, interaction: discord.Interaction):
        if interaction.user not in self.role_users:
            role_ar = "المافيا" if self.role_name == "mafia" else "الدكتور"
            embed = discord.Embed(
                description=f"انت مش من {role_ar}! العب بعيد.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if interaction.user != self.active_user:
            action_ar = "بالاغتيال" if self.role_name == "mafia" else "بالعلاج"
            embed = discord.Embed(
                description=f"القرعة اختارت {self.active_user.mention} عشان يقوم {action_ar} الليلة دي! استنى دورك.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        ephemeral_view = EphemeralActionView(self.targets)
        await interaction.response.send_message("اختار بسرعة قبل الوقت ما يخلص:", view=ephemeral_view, ephemeral=True)
        await ephemeral_view.wait()
        
        self.selected_id = ephemeral_view.selected_id
        if self.selected_id:
            for child in self.children: child.disabled = True
            try: await interaction.message.edit(view=self)
            except: pass
            self.stop()

class VoteDropdown(discord.ui.Select):
    def __init__(self, targets, view_parent):
        self.view_parent = view_parent
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in targets]
        super().__init__(placeholder="مين شاكك فيه؟ (اضغط للتصويت)", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user not in self.view_parent.alive_players:
            embed = discord.Embed(
                description="الميتين مابيتكلموش 💀",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        self.view_parent.votes[interaction.user] = int(self.values[0])
        await self.view_parent.update_embed(interaction)

class DayVoteView(discord.ui.View):
    def __init__(self, alive_players):
        super().__init__(timeout=20.0) 
        self.alive_players = alive_players
        self.votes = {} 
        self.add_item(VoteDropdown(alive_players[:25], self)) 

    @discord.ui.button(label="تفويت (Skip)", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.alive_players:
            embed = discord.Embed(
                description="الميتين مابيتكلموش 💀",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        self.votes[interaction.user] = "SKIP"
        await self.update_embed(interaction)

    async def update_embed(self, interaction):
        desc = "**الأصوات الحالية:**\n\n"
        for voter, target_id in self.votes.items():
            if target_id == "SKIP":
                desc += f"{voter.mention} ➔ ⏭️ **تفويت**\n"
            else:
                target_user = next((p for p in self.alive_players if p.id == target_id), None)
                if target_user:
                    desc += f"{voter.mention} ➔ 🔪 {target_user.mention}\n"
                    
        embed = interaction.message.embeds[0]
        embed.description = desc
        try: await interaction.response.edit_message(embed=embed)
        except: pass
        
        if len(self.votes) >= len(self.alive_players):
            self.stop()

class MafiaLobbyView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 60
        self.message = None
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        embed = lobby_embed(
            "أهلاً بيكو في الحفرة...",
            "المافيا بتتحرك في الضلمة... والدكتور بيحاول ينقذ ما يمكن إنقاذه.",
            self.players,
            self.time_left,
            max_players=25,
        )
        embed.color = discord.Color.dark_red()
        embed.set_image(url="attachment://menu.jpg")
        embed.set_footer(text="لعبة المافيا - كازينو الحفرة", icon_url=SERVER_LOGO_URL)
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
        if len(self.players) < 5:
            self.game_started = True
            cancel_embed = discord.Embed(
                title="❌ تم الإلغاء",
                description="الحفرة فاضية! (تحتاج 5 لاعبين على الأقل لبدء المافيا)",
                color=discord.Color.red()
            )
            await self.ctx.send(embed=cancel_embed)
            for child in self.children: child.disabled = True
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        for child in self.children: child.disabled = True
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(run_mafia_game(self.ctx, self.players))
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "Mafia", replace_task=self.timer_task):
            task.cancel()

    @discord.ui.button(label="انضمام (Join)", style=discord.ButtonStyle.success)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت خلاص!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if len(self.players) >= 25:
            embed = discord.Embed(
                description="الحد الأقصى 25 لاعب!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.user in self.players:
            embed = discord.Embed(
                description="أنت سجلت اسمك ياعم!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            self.players.append(interaction.user)
            await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="مغادرة (Leave)", style=discord.ButtonStyle.danger)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
                description="أنت مش في اللعبة أصلاً!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="ابدأ الآن", style=discord.ButtonStyle.primary)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        self.time_left += 10
        await interaction.response.edit_message(embed=self.get_embed())


# --- CORE GAME LOOP ---

class MafiaGameState:
    """Shared live state so shop items (scan/veto) can interact with a running game."""
    def __init__(self):
        self.game_name = 'mafia'
        self.alive = []
        self.mafias = []
        self.game_roles = {}


async def run_mafia_game(ctx, players):
    try:
        num = len(players)
        if num <= 10: mafia_count = 1
        elif num <= 15: mafia_count = 2
        else: mafia_count = 3
        
        doctor_count = 1
        civ_count = num - mafia_count - doctor_count
        
        roles_list = ['mafia']*mafia_count + ['doctor']*doctor_count + ['civilian']*civ_count
        random.shuffle(roles_list)
        
        game_roles = {}
        afk_tracker = {p: 0 for p in players} 
        mafias = []
        doctor = None
        
        for i, p in enumerate(players):
            role = roles_list[i]
            game_roles[p] = role
            if role == 'mafia': mafias.append(p)
            elif role == 'doctor': doctor = p
            
        alive = players.copy()

        # --- LIVE GAME STATE ---
        try:
            _state = MafiaGameState()
            _state.alive = alive
            _state.mafias = mafias
            _state.game_roles = game_roles
            if hasattr(ctx.bot, 'active_games') and ctx.channel.id in ctx.bot.active_games:
                ctx.bot.active_games[ctx.channel.id]['game_ref'] = _state
        except Exception:
            pass
        
        class RoleRevealView(discord.ui.View):
            def __init__(self): super().__init__(timeout=60)
            @discord.ui.button(label="👀 اعرف دورك السري", style=discord.ButtonStyle.primary)
            async def reveal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user not in alive:
                    embed = discord.Embed(
                        description="أنت مش في الجيم!",
                        color=discord.Color.red()
                    )
                    return await interaction.response.send_message(embed=embed, ephemeral=True)
                r = game_roles[interaction.user]
                ar_role = "🔪 مافيا" if r == 'mafia' else ("💉 دكتور" if r == 'doctor' else "👤 مواطن غلبان")
                embed = discord.Embed(
                    description=f"دورك في اللعبة هو: **{ar_role}**\nاحتفظ بالسر!",
                    color=discord.Color.blurple()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            description="🎲 **تم توزيع الأدوار!** اضغط على الزرار عشان تعرف دورك ايه في السر.",
            color=discord.Color.blurple()
        )
        reveal_view = RoleRevealView()
        reveal_view.message = await ctx.send(embed=embed, view=reveal_view)
        set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), reveal_view)
        await asyncio.sleep(5) 
        
        day_number = 1
        
        while True:
            embed = discord.Embed(
                description=f"🌙 **الليلة {day_number}**\nالليل دخل على الحفرة... المافيا بتتحرك.",
                color=discord.Color.dark_theme()
            )
            await ctx.send(embed=embed)
            
            bb_bytes = await generate_billboard(alive)
            bb_embed = discord.Embed(color=discord.Color.dark_theme())
            bb_embed.set_image(url="attachment://billboard.jpg")
            await ctx.send(embed=bb_embed, file=discord.File(fp=bb_bytes, filename="billboard.jpg"))
            await asyncio.sleep(2)

            alive_mafias = [m for m in mafias if m in alive]
            hitman = random.choice(alive_mafias) if alive_mafias else None
            
            mafia_target_id = None
            if hitman:
                try:
                    mafia_file = discord.File("mafiagame/mafia_looking.jpg", filename="mafia_looking.jpg")
                    targets = [p for p in alive if p not in alive_mafias]
                    m_view = SecretButtonView(hitman, alive_mafias, targets, "mafia")
                    
                    m_embed = discord.Embed(
                        description="الأوامر صدرت... 🔪 المافيا بتدور على ضحية دلوقتي.",
                        color=discord.Color.dark_red()
                    )
                    m_embed.set_image(url="attachment://mafia_looking.jpg")
                    
                    m_msg = await ctx.send(embed=m_embed, file=mafia_file, view=m_view)
                    m_view.message = m_msg
                    set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), m_view)
                    ctx.bot.loop.create_task(poke_reminder(hitman, "mafia", m_view))

                    await m_view.wait()
                    mafia_target_id = m_view.selected_id
                    
                    if mafia_target_id is None:
                        afk_tracker[hitman] += 1
                        if afk_tracker[hitman] >= 3:
                            alive.remove(hitman)
                            embed = discord.Embed(
                                description=f"⚠️ **النظام:** المافيا {hitman.mention} نام في العسل لـ 3 جولات ورا بعض! تم استبعاده من اللعبة للكسل.",
                                color=discord.Color.dark_grey()
                            )
                            await ctx.send(embed=embed)
                    else:
                        afk_tracker[hitman] = 0

                    for c in m_view.children: c.disabled = True
                    try: await m_msg.edit(view=m_view)
                    except discord.HTTPException: pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            else:
                await asyncio.sleep(5)
                
            await asyncio.sleep(1)

            doctor_target_id = None
            alive_doctors = [doctor] if doctor in alive else []
            if doctor in alive:
                try:
                    doc_file = discord.File("mafiagame/doctor_looking.jpg", filename="doctor_looking.jpg")
                    d_view = SecretButtonView(doctor, alive_doctors, alive, "doctor") 
                    
                    d_embed = discord.Embed(
                        description="💉 الدكتور بيلف في الشوارع بيدور على حد يعالجه...",
                        color=discord.Color.teal()
                    )
                    d_embed.set_image(url="attachment://doctor_looking.jpg")
                    
                    d_msg = await ctx.send(embed=d_embed, file=doc_file, view=d_view)
                    d_view.message = d_msg
                    set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), d_view)
                    ctx.bot.loop.create_task(poke_reminder(doctor, "doctor", d_view))

                    await d_view.wait()
                    doctor_target_id = d_view.selected_id

                    if doctor_target_id is None:
                        afk_tracker[doctor] += 1
                        if afk_tracker[doctor] >= 3:
                            alive.remove(doctor)
                            embed = discord.Embed(
                                description=f"⚠️ **النظام:** الدكتور {doctor.mention} اتأخر 3 جولات ومأنقذش حد! تم سحب رخصته واستبعاده من اللعبة.",
                                color=discord.Color.dark_grey()
                            )
                            await ctx.send(embed=embed)
                    else:
                        afk_tracker[doctor] = 0
                    
                    for c in d_view.children: c.disabled = True
                    try: await d_msg.edit(view=d_view)
                    except discord.HTTPException: pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            else:
                await asyncio.sleep(2) 
            
            await asyncio.sleep(1)

            embed = discord.Embed(
                description="🌅 **الشمس طلعت على الحفرة...**",
                color=discord.Color.gold()
            )
            await ctx.send(embed=embed)
            await asyncio.sleep(1) 
            
            if mafia_target_id:
                target_obj = next((p for p in alive if p.id == mafia_target_id), None)
                
                if target_obj:
                    if doctor_target_id == mafia_target_id:
                        save_bytes = await generate_action_image("doctor_saved.jpg", target_obj, "saved_person")
                        res_embed = discord.Embed(
                            description=f"المافيا حاولت تغتال **{target_obj.mention}** بليل... بس الدكتور كان صاحي ولحقه في آخر لحظة! 💉🩸",
                            color=discord.Color.green()
                        )
                        res_embed.set_image(url="attachment://saved.jpg")
                        await ctx.send(embed=res_embed, file=discord.File(fp=save_bytes, filename="saved.jpg"))
                    else:
                        alive.remove(target_obj)
                        if game_roles[target_obj] == 'doctor':
                            kill_bytes = await generate_action_image("doctor_killed.jpg", target_obj, "dead_doctor")
                            res_embed = discord.Embed(
                                description=f"المافيا غدرت بالدكتور **{target_obj.mention}**! الحفرة بقت من غير دكتور. 💀",
                                color=discord.Color.dark_theme()
                            )
                            res_embed.set_image(url="attachment://dead_doc.jpg")
                            await ctx.send(embed=res_embed, file=discord.File(fp=kill_bytes, filename="dead_doc.jpg"))
                        else:
                            kill_bytes = await generate_action_image("mafia_kill.jpg", target_obj, "dead_civilian")
                            res_embed = discord.Embed(
                                description=f"المافيا طيرت المواطن **{target_obj.mention}**... ولقينا جثته مرمية في الشارع. 💀",
                                color=discord.Color.dark_theme()
                            )
                            res_embed.set_image(url="attachment://dead_civ.jpg")
                            await ctx.send(embed=res_embed, file=discord.File(fp=kill_bytes, filename="dead_civ.jpg"))
            else:
                embed = discord.Embed(
                    description="الليلة دي عدت بسلام... المافيا نامت ومحدش مات. 🕊️",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)

            await asyncio.sleep(2) 

            alive_mafias = [m for m in mafias if m in alive]
            if len(alive_mafias) == 0:
                await town_wins(ctx, mafias)
                break
            elif len(alive_mafias) >= len(alive) - len(alive_mafias):
                await mafia_wins(ctx, mafias)
                break

            bb_bytes = await generate_billboard(alive)
            bb_embed = discord.Embed(
                description="الكل يتجمع! وقت المحاكمة... قدامكم **20 ثانية** تختاروا مين المافيا وتعدموه.",
                color=discord.Color.orange()
            )
            bb_embed.set_image(url="attachment://billboard.jpg")
            await ctx.send(embed=bb_embed, file=discord.File(fp=bb_bytes, filename="billboard.jpg"))
            
            embed = discord.Embed(
                title="⚖️ التصويت للإعدام",
                description="في انتظار تصويت المواطنين...",
                color=discord.Color.gold()
            )
            vote_view = DayVoteView(alive)
            vote_msg = await ctx.send(embed=embed, view=vote_view)
            vote_view.message = vote_msg
            set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), vote_view)
            await vote_view.wait() 
            
            for c in vote_view.children: c.disabled = True
            try: await vote_msg.edit(view=vote_view)
            except discord.HTTPException: pass
            
            tally = {}
            for voter, t_id in vote_view.votes.items():
                tally[t_id] = tally.get(t_id, 0) + 1
                
            if not tally:
                embed = discord.Embed(
                    description="محدش صوت أصلاً... اليوم عدى على الفاضي.",
                    color=discord.Color.light_grey()
                )
                await ctx.send(embed=embed)
            else:
                final_tally_desc = "**📊 النتيجة النهائية للتصويت:**\n\n"
                for t_id, count in tally.items():
                    if t_id == "SKIP":
                        final_tally_desc += f"⏭️ **تفويت التصويت:** {count} أصوات\n"
                    else:
                        final_tally_desc += f"🔪 <@{t_id}>: **{count}** أصوات\n"
                        
                embed = discord.Embed(
                    title="انتهى التصويت!",
                    description=final_tally_desc,
                    color=discord.Color.blurple()
                )
                await ctx.send(embed=embed)
                await asyncio.sleep(2)
                
                sorted_tally = sorted(tally.items(), key=lambda x: x[1], reverse=True)
                top_target_id = sorted_tally[0][0]
                top_votes = sorted_tally[0][1]
                
                tie = False
                if len(sorted_tally) > 1 and sorted_tally[1][1] == top_votes:
                    tie = True
                    
                if tie:
                    embed = discord.Embed(
                        description="الناس اختلفت والأصوات اتعادلت... محدش اتعدم النهاردة. ⚖️",
                        color=discord.Color.light_grey()
                    )
                    await ctx.send(embed=embed)
                elif top_target_id == "SKIP":
                    embed = discord.Embed(
                        description="الأغلبية اختارت تفوت التصويت... محدش هيتعدم النهاردة. ⏭️",
                        color=discord.Color.light_grey()
                    )
                    await ctx.send(embed=embed)
                else:
                    hanged_player = next((p for p in alive if p.id == top_target_id), None)
                    if hanged_player:
                        # --- VETO RESOLUTION ---
                        try:
                            cur_cog = ctx.bot.get_cog('Currency')
                        except Exception:
                            cur_cog = None
                        if cur_cog and cur_cog.get_item_count(hanged_player.id, "veto") > 0:
                            cur_cog.remove_item(hanged_player.id, "veto", 1)
                            embed = discord.Embed(
                                description=f"⛔ **{hanged_player.mention}** استخدم **فيتو**! الناس صوتت ضده بس الفيتو لغى الإعدام... عاش يا وحش!",
                                color=discord.Color.gold()
                            )
                            await ctx.send(embed=embed)
                            await asyncio.sleep(2)
                            alive_mafias = [m for m in mafias if m in alive]
                            if len(alive_mafias) == 0:
                                await town_wins(ctx, mafias)
                                break
                            elif len(alive_mafias) >= len(alive) - len(alive_mafias):
                                await mafia_wins(ctx, mafias)
                                break
                            day_number += 1
                            continue
                        alive.remove(hanged_player)
                        r = game_roles[hanged_player]
                        ar_role = "مافيا 🔪" if r == 'mafia' else ("دكتور 💉" if r == 'doctor' else "مواطن غلبان 👤")
                        embed = discord.Embed(
                            description=f"🚨 الناس قررت تعدم **{hanged_player.mention}**! وبعد ما فتشوه اكتشفوا إنه كان: **{ar_role}**.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)

            await asyncio.sleep(2) 

            alive_mafias = [m for m in mafias if m in alive]
            if len(alive_mafias) == 0:
                await town_wins(ctx, mafias)
                break
            elif len(alive_mafias) >= len(alive) - len(alive_mafias):
                await mafia_wins(ctx, mafias)
                break
                
            day_number += 1
            
    except asyncio.CancelledError:
        raise
    finally:
        release_game_task(ctx.bot, ctx.channel.id, asyncio.current_task())

async def town_wins(ctx, mafias):
    reveal_bytes = await generate_mafia_reveal(mafias)
    embed = discord.Embed(
        title="🎉 المواطنين انتصروا!",
        description="المواطنين قدروا ينضفوا الحفرة من المافيا! النور رجع للمدينة.",
        color=discord.Color.green()
    )
    embed.set_image(url="attachment://reveal.jpg")
    await ctx.send(embed=embed, file=discord.File(fp=reveal_bytes, filename="reveal.jpg"), view=WatermarkView())

async def mafia_wins(ctx, mafias):
    reveal_bytes = await generate_mafia_reveal(mafias)
    embed = discord.Embed(
        title="💀 المافيا سيطرت!",
        description="المافيا مسكت الحفرة بالكامل... المواطنين ماتوا والظلام كسب.",
        color=discord.Color.dark_red()
    )
    embed.set_image(url="attachment://reveal.jpg")
    await ctx.send(embed=embed, file=discord.File(fp=reveal_bytes, filename="reveal.jpg"), view=WatermarkView())

# --- COG COMMANDS ---
class Mafia(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='مافيا', description='بدء لعبة المافيا')
    @is_staff_or_owner()
    async def start_mafia(self, ctx):
        """يبدأ لعبة المافيا في الحفرة"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = None
        try:
            menu_file = discord.File("mafiagame/menu.jpg", filename="menu.jpg")
            view = MafiaLobbyView(ctx)
            if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Mafia lobby", view=view):
                view.timer_task.cancel()
                return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
            view.message = await ctx.send(embed=view.get_embed(), file=menu_file, view=view)
            if not owns_game_task(self.bot, ctx.channel.id, view.timer_task):
                for child in view.children:
                    child.disabled = True
                await view.message.edit(view=view)
        except Exception as e:
            if view is not None:
                release_game_task(self.bot, ctx.channel.id, view.timer_task)
                view.timer_task.cancel()
            embed = discord.Embed(
                description=f"⚠️ تأكد من وجود مجلد `mafiagame` والصور بالأسماء الصحيحة. \nالخطأ: {e}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    @commands.hybrid_command(name='اكشف', aliases=['scan', 'كشف'], description='استخدم أداة كشف المافيا')
    async def mafia_scan(self, ctx):
        """🔍 استخدم كشف مافيا: تلميح عن مافيا واحد عايش (بيشتغل في قناة اللعبة)"""
        cur_cog = self.bot.get_cog('Currency')
        if cur_cog is None:
            return await ctx.send("❌ سيستم الفلوس واقع! كلم نجلاوي.")
        if cur_cog.get_item_count(ctx.author.id, "mafia_scan") <= 0:
            return await ctx.send(f"❌ {ctx.author.mention} معكش **كشف مافيا**! اشتريه من الشوب بـ 150 (`+رصيد` > Shop).")
        if not hasattr(self.bot, 'active_games') or ctx.channel.id not in self.bot.active_games:
            return await ctx.send("❌ مفيش لعبة مافيا شغالة هنا!")
        game_data = self.bot.active_games[ctx.channel.id]
        game = game_data.get('game_ref')
        if game is None or getattr(game, 'game_name', '') != 'mafia':
            return await ctx.send("❌ الأمر ده بيشتغل في لعبة المافيا بس!")
        if ctx.author not in game.alive:
            return await ctx.send("💀 الميتين مابيكشفوش!")
        alive_mafias = [m for m in game.mafias if m in game.alive and m != ctx.author]
        if not alive_mafias:
            return await ctx.send("❌ مفيش مافيا عايشة غيرك (او انت المافيا)!")
        if not cur_cog.remove_item(ctx.author.id, "mafia_scan", 1):
            return await ctx.send(f"❌ {ctx.author.mention} أداة الكشف اتستخدمت أو مش موجودة. راجع شنطتك وحاول تاني.")
        hint = random.choice(alive_mafias)
        masked = hint.display_name[:2] + "***"
        embed = discord.Embed(
            title="🔍 نتيجة الكشف (سري)",
            description=f"الجهاز بيقول ان **{masked}** ({hint.mention}) عليه حركات مافيا... 👀\n(اتخصم منك 1 كشف)",
            color=discord.Color.gold()
        )
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            try:
                cur_cog.add_item(ctx.author.id, "mafia_scan", 1)
            except Exception as refund_error:
                print(f"Failed to refund mafia_scan after DM Forbidden: {refund_error}")
                return await ctx.send("❌ افتح الخاص؛ فشل إرجاع أداة الكشف تلقائيًا، كلم الستاف.")
            return await ctx.send(f"❌ {ctx.author.mention} افتح الرسائل الخاصة؛ رجعنا أداة الكشف لشنطتك.")
        except discord.HTTPException as send_error:
            try:
                cur_cog.add_item(ctx.author.id, "mafia_scan", 1)
            except Exception as refund_error:
                print(f"Failed to refund mafia_scan after DM error: {refund_error}")
                return await ctx.send("❌ فشل إرسال النتيجة وإرجاع الأداة تلقائيًا؛ كلم الستاف.")
            print(f"Failed to deliver mafia_scan result: {send_error}")
            return await ctx.send("❌ مقدرناش نبعت النتيجة في الخاص؛ رجعنا أداة الكشف لشنطتك.")

        await ctx.send(f"📩 {ctx.author.mention} نتيجة الكشف اتبعتتلك في الخاص! (عشان اللعبة ماتتحرقش)")

async def setup(bot):
    await bot.add_cog(Mafia(bot))