import asyncio
import logging
import random
from functools import partial

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

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
    THEME,
    StatusPanel,
    lobby_embed,
    report_game_failure,
    result_embed,
)

logger = logging.getLogger(__name__)


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
            style = discord.ButtonStyle.secondary if round_type == 'normal' else discord.ButtonStyle.secondary
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
                btn.style = discord.ButtonStyle.secondary 
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


class ChairsLobby(LayoutView):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 35
        self.message = None
        
        self.logo_url = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام (Join)", "chairs_join", "join_btn", None),
            ("مغادرة (Leave)", "chairs_leave", "leave_btn", None),
            ("ابدأ الآن", "chairs_start", "start_btn", None),
            ("+10 ثواني", "chairs_add_time", "add_time_btn", "⏱️"),
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

    def _panel_content(self):
        embed = self.get_embed()
        description = embed.description or ""
        return f"# {embed.title or 'لعبة الكراسي الموسيقية'}\\n{description}\\n\\n-# كازينو الحفرة • {config.BRAND}"

    def _refresh_panel(self):
        self._panel_text.content = self._panel_content()[:4000]

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

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
                    logger.debug("Could not refresh musical chairs lobby", exc_info=True)

    async def auto_start(self):
        if self.game_started: return
        if len(self.players) < 2:
            self.game_started = True
            self._panel_text.content = "# العدد غير كافي\\nمحتاجين لاعبين على الأقل عشان الجولة تبدأ."
            self._disable_buttons()
            if self.message: await self.message.edit(view=self)
            return

        self.game_started = True
        self._panel_text.content = "# جاري تجهيز الكراسي الموسيقية\\nهنبدأ الجولة دلوقتي. جهّز نفسك."
        self._disable_buttons()
        if self.message: await self.message.edit(view=self)
        
        task = self.ctx.bot.loop.create_task(self.run_game_loop())
        if not register_game_task(self.ctx.bot, self.ctx.channel.id, task, self.ctx.author.id, "Chairs", replace_task=self.timer_task):
            task.cancel()

    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            return await interaction.response.send_message(
                view=StatusPanel("اللعبة بدأت", "مش هنقدر نضيف لاعبين بعد بداية الجولة.", "warning"),
                ephemeral=True,
            )
        if len(self.players) >= 25:
            return await interaction.response.send_message(
                view=StatusPanel("اللوبي اكتمل", "وصلنا للحد الأقصى: 25 لاعب.", "warning"),
                ephemeral=True,
            )
        if interaction.user in self.players:
            await interaction.response.send_message(
                view=StatusPanel("أنت موجود", "أنت مسجل بالفعل في اللوبي.", "warning"),
                ephemeral=True,
            )
        else:
            self.players.append(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)

    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_started:
            return await interaction.response.send_message(
                view=StatusPanel("اللعبة بدأت", "اللوبي اتقفل ومبقاش متاح تغيير اللاعبين.", "warning"),
                ephemeral=True,
            )
        if interaction.user in self.players:
            self.players.remove(interaction.user)
            self._refresh_panel()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message(
                view=StatusPanel("مش في اللوبي", "انضم الأول قبل ما تحاول تغادر.", "warning"),
                ephemeral=True,
            )

    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                view=StatusPanel("مش مسموح", "صاحب اللوبي فقط يقدر يبدأ الجولة أو يمدد الوقت.", "warning"),
                ephemeral=True,
            )
        await interaction.response.defer()
        self.time_left = 0
        await self.auto_start()

    async def add_time_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="للأدمن فقط!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        self.time_left += 10
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

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
                    action_view.message = msg
                    set_game_view(self.ctx.bot, self.ctx.channel.id, asyncio.current_task(), action_view)
                    await action_view.wait()
                    
                    for child in action_view.children: child.disabled = True
                    try:
                        await msg.edit(view=action_view)
                    except discord.HTTPException:
                        logger.debug("Could not disable musical chairs action buttons", exc_info=True)
                    
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
                    action_view.message = msg
                    set_game_view(self.ctx.bot, self.ctx.channel.id, asyncio.current_task(), action_view)
                    await action_view.wait()
                    
                    for child in action_view.children: child.disabled = True
                    try:
                        await msg.edit(view=action_view)
                    except discord.HTTPException:
                        logger.debug("Could not disable musical chairs action buttons", exc_info=True)
                    
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
                embed = result_embed(
                    title="🏆 بطل الكراسي الموسيقية!",
                    description=f"🎉 الفائز النهائي والناجي الوحيد هو **{winner.mention}**!",
                    outcome="win",
                    thumbnail_url=winner.display_avatar.url,
                )
                await self.ctx.send(content=f"مبروك يا نجم {winner.mention} 👑", embed=embed, view=WatermarkView())

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Musical chairs game loop failed")
            await report_game_failure(
                self.ctx,
                "توقفت لعبة الكراسي بسبب خطأ",
                "حصل خطأ تقني أثناء الجولة. سجّلنا التفاصيل، واتقفلت اللوحة الحالية بدل تكرار رسائل جديدة.",
                fallback_view=self,
            )
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Chairs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='كراسي', description='بدء لعبة الكراسي الموسيقية')
    @is_staff_or_admin()
    async def start_chairs(self, ctx):
        """يبدأ لعبة الكراسي الموسيقية"""
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        view = ChairsLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Chairs lobby", view=view):
            view.timer_task.cancel()
            return await ctx.send("❌ فيه لعبة أو لوبي شغال في الروم بالفعل.")
        try:
            view.message = await ctx.send(view=view)
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