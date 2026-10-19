import discord
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task
import asyncio
import random
import config

from cogs.casino.permissions import is_staff_or_owner
from cogs.utility.theme import lobby_embed


# --- UI VIEWS ---

class WatermarkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Developed by @neglawy", style=discord.ButtonStyle.secondary, disabled=True))

class ChairActionView(discord.ui.View):
    def __init__(self, active_players, num_chairs, round_type):
        super().__init__(timeout=15.0 if round_type == 'normal' else 4.0)
        self.active_players = active_players
        self.num_chairs = num_chairs
        self.round_type = round_type
        
        self.seated_players = set()
        self.eliminated_fools = set()
        
        for i in range(num_chairs):
            style = discord.ButtonStyle.primary if round_type == 'normal' else discord.ButtonStyle.danger
            btn = discord.ui.Button(style=style, label="🪑", custom_id=f"chair_{i}")
            btn.callback = self.make_callback(btn)
            self.add_item(btn)

    def make_callback(self, btn):
        async def callback(interaction: discord.Interaction):
            user = interaction.user
            
            if user not in self.active_players:
                embed = discord.Embed(
                    description="أنت مش في اللعبة يا كابتن! اتفرج بصمت.",
                    color=discord.Color.red()
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            
            if self.round_type == 'normal':
                if user in self.seated_players:
                    embed = discord.Embed(
                        description="أنت قعدت خلاص! سيب فرصة لغيرك يا بجم.",
                        color=discord.Color.red()
                    )
                    return await interaction.response.send_message(embed=embed, ephemeral=True)
                
                if btn.disabled:
                    embed = discord.Embed(
                        description="الكرسي ده اتاخد! دور على كرسي فاضي بسرعة 🏃‍♂️💨!",
                        color=discord.Color.red()
                    )
                    return await interaction.response.send_message(embed=embed, ephemeral=True)
                
                if len(self.seated_players) >= self.num_chairs:
                    embed = discord.Embed(
                        description="للأسف الكراسي خلصت!",
                        color=discord.Color.red()
                    )
                    return await interaction.response.send_message(embed=embed, ephemeral=True)
                    
                self.seated_players.add(user)
                btn.style = discord.ButtonStyle.success 
                btn.disabled = True
                btn.label = user.display_name[:6] 
                
                await interaction.response.edit_message(view=self)
                
                if len(self.seated_players) >= self.num_chairs:
                    self.stop()
                    
            elif self.round_type == 'forbidden':
                if user in self.eliminated_fools:
                    return await interaction.response.defer()
                    
                self.eliminated_fools.add(user)
                btn.disabled = True
                btn.label = "💥"
                await interaction.response.edit_message(view=self)
                
                embed = discord.Embed(
                    description=f"🚨 **{user.mention}** استعجل وقعد على كرسي مكسور وطار بره اللعبة!",
                    color=discord.Color.red()
                )
                await interaction.channel.send(embed=embed)
                
        return callback


class ChairsLobby(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 35
        self.message = None
        
        self.logo_url = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
        self.timer_task = self.ctx.bot.loop.create_task(self.run_timer())

    def get_embed(self):
        rules = (
            "**طريقة اللعب:**\n"
            "1- هتظهر كراسي رمادية في الشات لمدة 5 ثواني عشان تستعد.\n"
            "2- لو الكراسي قلبت **أزرق**، اقعد بسرعة قبل ما الكراسي تخلص!\n"
            "3- لو الكراسي قلبت **أحمر** (فخ)، إياك تضغط! اللي هيضغط هيطير.\n"
            "4- آخر واحد يتبقى هو الفائز.\n\n"
        )
        embed = lobby_embed(
            "🪑 لعبة الكراسي الموسيقية",
            rules.strip(),
            self.players,
            self.time_left,
            max_players=25,
        )
        embed.set_thumbnail(url=self.logo_url)
        embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}", icon_url=self.logo_url)
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
            description="🚀 جاري تجهيز الكراسي الموسيقية...",
            color=discord.Color.blue()
        )
        await self.ctx.send(embed=embed, delete_after=3)
        for child in self.children: child.disabled = True
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(self.run_game_loop())
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "Chairs", replace_task=self.timer_task):
            task.cancel()

    @discord.ui.button(label="انضمام (Join)", style=discord.ButtonStyle.success)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            embed = discord.Embed(
                description="اللعبة بدأت ياعم!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if len(self.players) >= 25:
            embed = discord.Embed(
                description="اكتمل العدد (25 لاعب)!",
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

    @discord.ui.button(label="مغادرة (Leave)", style=discord.ButtonStyle.danger)
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
                description="أنت لست في اللعبة أصلاً!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="ابدأ الآن", style=discord.ButtonStyle.primary)
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

    # --- MUSICAL CHAIRS LOOP ---
    async def run_game_loop(self):
        try:
            active_players = self.players.copy()
            round_num = 1

            while len(active_players) > 1:
                num_chairs = len(active_players) - 1
                is_forbidden = False if round_num == 1 else (random.random() < 0.25)

                prep_view = discord.ui.View()
                for _ in range(num_chairs):
                    prep_view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="🪑", disabled=True))
                
                embed = discord.Embed(
                    title=f"🪑 الجولة {round_num}",
                    description=f"استعد... الموسيقى شغالة 🎶\nالكراسي بتجهز... خليك جاهز!",
                    color=discord.Color.dark_grey()
                )
                embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}", icon_url=self.logo_url)
                
                msg = await self.ctx.send(embed=embed, view=prep_view)
                await asyncio.sleep(random.uniform(3.0, 6.0))

                round_type = 'forbidden' if is_forbidden else 'normal'
                action_view = ChairActionView(active_players, num_chairs, round_type)

                if round_type == 'normal':
                    embed.description = f"🛑 **الموسيقى وقفت!! اقعد بسرعة!!**\n(متاح {num_chairs} كراسي بس)"
                    embed.color = discord.Color.blue()
                    await msg.edit(embed=embed, view=action_view)
                    
                    await action_view.wait()
                    
                    for child in action_view.children: child.disabled = True
                    try: await msg.edit(view=action_view)
                    except: pass
                    
                    losers = [p for p in active_players if p not in action_view.seated_players]
                    if losers:
                        loser_mentions = " ".join([l.mention for l in losers])
                        embed = discord.Embed(
                            description=f"❌ **اللي كان بطيء ومالحقش كرسي وطار من اللعبة:** {loser_mentions}",
                            color=discord.Color.red()
                        )
                        await self.ctx.send(embed=embed)
                    
                    active_players = list(action_view.seated_players)
                    
                else: 
                    embed.description = f"🚨 **فخ!! كراسي مكسورة (حمراء)!**\nإياك تقعد!!"
                    embed.color = discord.Color.red()
                    await msg.edit(embed=embed, view=action_view)
                    
                    await action_view.wait()
                    
                    for child in action_view.children: child.disabled = True
                    try: await msg.edit(view=action_view)
                    except: pass
                    
                    if action_view.eliminated_fools:
                        active_players = [p for p in active_players if p not in action_view.eliminated_fools]
                    else:
                        embed = discord.Embed(
                            description="👏 وحوش! محدش وقع في الفخ وكله ركز.",
                            color=discord.Color.green()
                        )
                        await self.ctx.send(embed=embed)

                if len(active_players) == 0:
                    embed = discord.Embed(
                        description="💀 كله طار من اللعبة! مفيش فائز الجيم ده.",
                        color=discord.Color.dark_grey()
                    )
                    await self.ctx.send(embed=embed, view=WatermarkView())
                    break
                    
                round_num += 1
                await asyncio.sleep(3) 

            # --- WINNER ANNOUNCEMENT ---
            if len(active_players) == 1:
                winner = active_players[0]
                embed = discord.Embed(
                    title="🏆 بطل الكراسي الموسيقية!",
                    description=f"🎉 الفائز النهائي والناجي الوحيد هو **{winner.mention}**!",
                    color=discord.Color.gold()
                )
                embed.set_thumbnail(url=winner.display_avatar.url)
                embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}", icon_url=self.logo_url)
                await self.ctx.send(content=f"مبروك يا نجم {winner.mention} 👑", embed=embed, view=WatermarkView())

        except asyncio.CancelledError:
            pass
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Chairs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='كراسي', description='بدء لعبة الكراسي الموسيقية')
    @is_staff_or_owner()
    async def start_chairs(self, ctx):
        """يبدأ لعبة الكراسي الموسيقية"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = ChairsLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Chairs lobby", view=view):
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
    await bot.add_cog(Chairs(bot))