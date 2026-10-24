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
from cogs.utility.theme import (
    embed_to_v2,
    THEME,
    StatusPanel,
    lobby_embed,
    report_game_failure,
    result_embed,
    send_status,
    try_edit_view,
)

logger = logging.getLogger(__name__)


# --- UI VIEWS ---

class ChairPrepView(LayoutView):
    def __init__(self, round_num, num_chairs):
        super().__init__(timeout=30)
        self._panel_text = TextDisplay(
            f"# الجولة {round_num} — استعد\nالموسيقى شغالة. الكراسي بتجهز؛ استعد للضغط لما الموسيقى تقف."
        )
        disabled_buttons = [
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="🪑",
                custom_id=f"chairs_prep_{round_num}_{index}",
                disabled=True,
            )
            for index in range(num_chairs)
        ]
        rows = [
            ActionRow(*disabled_buttons[index:index + 5])
            for index in range(0, len(disabled_buttons), 5)
        ]
        self.add_item(Container(self._panel_text, Separator(), *rows, accent_color=THEME["grey"]))


class ChairActionView(LayoutView):
    def __init__(self, active_players, num_chairs, round_type, round_num=1):
        super().__init__(timeout=15.0 if round_type == "normal" else 4.0)
        self.active_players = active_players
        self.num_chairs = num_chairs
        self.round_type = round_type
        self.round_num = round_num
        self.seated_players = set()
        self.eliminated_fools = set()
        self.message = None

        if round_type == "normal":
            description = (
                f"# الجولة {round_num} — الموسيقى وقفت!\n"
                f"اقعد بسرعة. متاح {num_chairs} كراسي بس."
            )
            accent = THEME["teal"]
        else:
            description = (
                f"# الجولة {round_num} — فخ!\n"
                "الكراسي مكسورة. متضغطش على أي كرسي وإلا هتخرج من الجولة."
            )
            accent = THEME["red_dark"]

        self._panel_text = TextDisplay(description)
        self._buttons = []
        for index in range(num_chairs):
            button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="💥" if round_type == "forbidden" else "🪑",
                custom_id=f"chair_{round_num}_{index}",
            )
            button.callback = self.make_callback(button)
            self._buttons.append(button)

        rows = [
            ActionRow(*self._buttons[index:index + 5])
            for index in range(0, len(self._buttons), 5)
        ]
        self.add_item(
            Container(self._panel_text, Separator(), *rows, accent_color=accent)
        )

    def disable_controls(self):
        for button in self._buttons:
            button.disabled = True

    def make_callback(self, button):
        async def callback(interaction: discord.Interaction):
            user = interaction.user
            if user not in self.active_players:
                return await interaction.response.send_message(
                    view=StatusPanel("مش في الجولة", "أنت مش مشارك في لعبة الكراسي دي.", "warning"),
                    ephemeral=True,
                )

            if self.round_type == "normal":
                if user in self.seated_players:
                    return await interaction.response.send_message(
                        view=StatusPanel("أنت قعدت بالفعل", "اختيارك اتحسب بالفعل في الجولة دي.", "warning"),
                        ephemeral=True,
                    )
                if button.disabled:
                    return await interaction.response.send_message(
                        view=StatusPanel("الكرسي اتاخد", "اختار كرسي تاني بسرعة.", "warning"),
                        ephemeral=True,
                    )
                if len(self.seated_players) >= self.num_chairs:
                    return await interaction.response.send_message(
                        view=StatusPanel("الكراسي خلصت", "كل الكراسي المتاحة اتحجزت.", "warning"),
                        ephemeral=True,
                    )

                self.seated_players.add(user)
                button.disabled = True
                button.label = user.display_name[:6]
                self._panel_text.content = (
                    f"# الجولة {self.round_num} — الموسيقى وقفت!\n"
                    f"اللي قعدوا: {len(self.seated_players)} من {self.num_chairs}."
                )
                await interaction.response.edit_message(view=self)
                if len(self.seated_players) >= self.num_chairs:
                    self.stop()
            else:
                if user in self.eliminated_fools:
                    return await interaction.response.send_message(
                        view=StatusPanel("اختيار محسوب", "أنت خرجت بالفعل بسبب ضغط كرسي مكسور.", "warning"),
                        ephemeral=True,
                    )
                if button.disabled:
                    return await interaction.response.send_message(
                        view=StatusPanel("الكرسي اتحسب", "الكرسي ده اتضغط عليه بالفعل؛ اختار كرسي آخر.", "warning"),
                        ephemeral=True,
                    )
                self.eliminated_fools.add(user)
                button.disabled = True
                button.label = "💥"
                self._panel_text.content = (
                    f"# فخ الجولة {self.round_num}\n"
                    f"{user.mention} ضغط كرسي مكسور وخرج من اللعبة."
                )
                await interaction.response.edit_message(view=self)

        return callback


class ChairsLobby(LayoutView):
    MAX_TIME_EXTENSIONS = 3
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 35
        self.extensions_left = self.MAX_TIME_EXTENSIONS
        self.message = None
        
        self.logo_url = "https://media.discordapp.net/attachments/1509650275164749994/1509998350500364328/c51bafdd9c1feb13e22458c35067c718.webp"
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام", "chairs_join", "join_btn", None),
            ("مغادرة", "chairs_leave", "leave_btn", None),
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
            "1- هتظهر لوحة تجهيز لمدة 5 ثواني عشان تستعد.\n"
            "2- في الجولة العادية، لوحة الجولة بتكون بلون تركوازي؛ اضغط على كرسي متاح بسرعة، لأن الكراسي أقل من اللاعبين.\n"
            "3- في جولة الفخ، اللوحة بتكون حمراء وكل الأزرار عليها 💥؛ متضغطش على أي زر وإلا هتخرج.\n"
            "4- آخر واحد يتبقى هو الفائز.\n\n"
        )
        embed = lobby_embed(
            "🪑 لعبة الكراسي الموسيقية",
            rules.strip(),
            self.players,
            self.time_left,
            max_players=25,
            host=self.ctx.author,
            min_players=2,
        )
        embed.set_thumbnail(url=self.logo_url)
        embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}", icon_url=self.logo_url)
        return embed

    def _panel_content(self):
        embed = self.get_embed()
        description = embed.description or ""
        return (
            f"# {embed.title or 'لعبة الكراسي الموسيقية'}\n{description}\n\n"
            f"**تمديدات الوقت المتبقية:** {self.extensions_left}\n\n"
            f"-# كازينو الحفرة • {config.BRAND}"
        )[:4000]

    def _refresh_panel(self):
        self._panel_text.content = self._panel_content()

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
            self._panel_text.content = "# العدد غير كافي\nمحتاجين لاعبين على الأقل عشان الجولة تبدأ."
            self._disable_buttons()
            await try_edit_view(self.message, self, context="chairs_lobby_cancelled")
            return

        self.game_started = True
        self._panel_text.content = "# جاري تجهيز الكراسي الموسيقية\nهنبدأ الجولة دلوقتي. جهّز نفسك."
        self._disable_buttons()
        await try_edit_view(self.message, self, context="chairs_lobby_start")
        
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
            return await interaction.response.send_message(
                view=StatusPanel("مش مسموح", "صاحب اللوبي فقط يقدر يمدد وقت التسجيل.", "warning"),
                ephemeral=True,
            )
        if self.game_started:
            return await interaction.response.send_message(
                view=StatusPanel("اللوبي اتقفل", "الجولة بدأت بالفعل.", "warning"),
                ephemeral=True,
            )
        if self.extensions_left <= 0:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "وصلنا للحد الأقصى",
                    "استخدمت كل تمديدات الوقت المتاحة. ابدأ الجولة أو استنى العدّ التنازلي.",
                    "warning",
                ),
                ephemeral=True,
            )
        self.extensions_left -= 1
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

                prep_view = ChairPrepView(round_num, num_chairs)
                msg = await self.ctx.send(view=prep_view)
                await asyncio.sleep(random.uniform(3.0, 6.0))

                round_type = "forbidden" if is_forbidden else "normal"
                action_view = ChairActionView(
                    active_players,
                    num_chairs,
                    round_type,
                    round_num=round_num,
                )
                await msg.edit(view=action_view)
                action_view.message = msg
                set_game_view(self.ctx.bot, self.ctx.channel.id, asyncio.current_task(), action_view)
                await action_view.wait()

                if round_type == "normal":
                    losers = [
                        player for player in active_players
                        if player not in action_view.seated_players
                    ]
                    active_players = list(action_view.seated_players)
                    if losers:
                        loser_mentions = " ".join(player.mention for player in losers)
                        action_view._panel_text.content = (
                            f"# نهاية الجولة {round_num}\n"
                            f"اللي ما لحقش كرسي وخرج من اللعبة: {loser_mentions}"
                        )
                    else:
                        action_view._panel_text.content = (
                            f"# نهاية الجولة {round_num}\nكل الكراسي اتحجزت."
                        )
                else:
                    if action_view.eliminated_fools:
                        removed = action_view.eliminated_fools
                        active_players = [
                            player for player in active_players if player not in removed
                        ]
                        removed_mentions = " ".join(player.mention for player in removed)
                        action_view._panel_text.content = (
                            f"# نهاية الفخ — الجولة {round_num}\n"
                            f"اللاعبون اللي ضغطوا كرسي مكسور وخرجوا: {removed_mentions}"
                        )
                    else:
                        action_view._panel_text.content = (
                            f"# نهاية الفخ — الجولة {round_num}\n"
                            "محدش وقع في الفخ؛ كله ركز."
                        )

                action_view.disable_controls()
                try:
                    await msg.edit(view=action_view)
                except discord.HTTPException:
                    logger.debug("Could not disable musical chairs V2 controls", exc_info=True)

                if len(active_players) == 0:
                    await self.ctx.send(
                        view=StatusPanel(
                            "انتهت لعبة الكراسي",
                            "كله خرج من اللعبة؛ مفيش فائز في الجولة دي.",
                            "warning",
                        )
                    )
                    break
                    
                round_num += 1
                await asyncio.sleep(3) 

            # --- WINNER ANNOUNCEMENT ---
            if len(active_players) == 1:
                winner = active_players[0]
                await self.ctx.send(
                    view=StatusPanel(
                        "بطل الكراسي الموسيقية!",
                        f"الفائز النهائي والناجي الوحيد هو {winner.mention}.",
                        "success",
                    )
                )

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
    async def start_chairs(self, ctx):
        """يبدأ لعبة الكراسي الموسيقية"""
        if is_active_game(self.bot, ctx.channel.id):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ واحدة جديدة.",
                "warning",
            )
        view = ChairsLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Chairs lobby", view=view):
            view.timer_task.cancel()
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ واحدة جديدة.",
                "warning",
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
    await bot.add_cog(Chairs(bot))