import asyncio
import logging
import math
import os
import random
import time
from functools import partial

import discord
from discord.ext import commands
from discord.ui import (
    ActionRow,
    Container,
    LayoutView,
    MediaGallery,
    Separator,
    TextDisplay,
)

import config
from cogs.casino.game_assets import SERVER_LOGO_URL
from cogs.casino.game_registry import (
    is_active_game,
    owns_game_task,
    register_game_task,
    release_game_task,
    set_game_view,
)
from cogs.casino.roulette_art import generate_sniper_image
from cogs.casino.roulette_views import KickView
from cogs.utility.theme import (
    add_developer_credit,
    private_status,
    THEME,
    StatusPanel,
    embed_to_v2,
    lobby_embed,
    report_game_failure,
    result_embed,
    send_status,
    try_edit_view,
    wait_for_view_fixed_timeout,
)

logger = logging.getLogger(__name__)



# --- UI VIEWS ---
class RouletteLobby(LayoutView):
    MAX_TIME_EXTENSIONS = 3
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.players = []
        self.game_started = False
        self.time_left = 35
        self.extensions_left = self.MAX_TIME_EXTENSIONS
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام", "roulette_join", "join_btn", None),
            ("مغادرة", "roulette_leave", "leave_btn", None),
            ("ابدأ الآن", "roulette_start", "start_btn", None),
            ("+10 ثواني", "roulette_add_time", "add_time_btn", "⏱️"),
        ):
            button = discord.ui.Button(
                label=label,
                custom_id=custom_id,
                style=discord.ButtonStyle.secondary,
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
        rules = ""
        embed = lobby_embed(
            "🎯 روليت الحفرة",
            rules,
            self.players,
            self.time_left,
            max_players=45,
            host=self.ctx.author,
            min_players=2,
        )
        embed.color = discord.Color.dark_purple()
        return embed

    def _panel_content(self, embed=None):
        embed = embed or self.get_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        return (
            title
            + f"{embed.description or ''}\n\n"
            + f"**تمديدات متاحة:** {self.extensions_left}\n\n"
            + f"-# كازينو الحفرة • {config.BRAND}"
        )[:4000]

    def _sync_extension_control(self):
        exhausted = self.game_started or self.extensions_left <= 0
        for button in self._buttons:
            if button.custom_id == "roulette_add_time":
                button.disabled = exhausted

    def _refresh_panel(self, embed=None):
        self._panel_text.content = self._panel_content(embed)
        self._sync_extension_control()

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _private_notice(self, interaction, title, description):
        return await private_status(interaction, title, description, "warning")

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
            await try_edit_view(self.message, self, context="roulette_lobby_cancelled")
            return

        self.game_started = True
        self._panel_text.content = "# جاري تجهيز الأسلحة\nاللوبي اتقفل. الجولة هتبدأ دلوقتي."
        self._disable_buttons()
        await try_edit_view(self.message, self, context="roulette_lobby_start")
        
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
                description="أنت مسجل بالفعل في اللوبي.",
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
                description="أنت مش مسجل في اللوبي، فلازم تنضم الأول قبل المغادرة.",
                color=discord.Color.red()
            )
            await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")

    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            embed = discord.Embed(
                description="صاحب اللوبي فقط يقدر يتحكم في بدء الجولة.",
                color=discord.Color.red()
            )
            return await self._private_notice(interaction, embed.title or "تنبيه", embed.description or "الطلب غير متاح.")
        if self.game_started:
            return await private_status(
                interaction,
                "اللوبي اتقفل",
                "الجولة بدأت بالفعل؛ ضغطة البداية التانية ما غيّرتش حالة اللعبة.",
                "warning",
            )
        await interaction.response.defer()
        if self.game_started:
            return await private_status(
                interaction,
                "اللوبي اتقفل",
                "الجولة بدأت بالفعل؛ ضغطة البداية التانية ما غيّرتش حالة اللعبة.",
                "warning",
            )
        self.time_left = 0
        await self.auto_start()

    async def add_time_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await self._private_notice(
                interaction,
                "مش مسموح",
                "صاحب اللوبي فقط يقدر يمدد وقت التسجيل.",
            )
        if self.game_started:
            return await self._private_notice(interaction, "اللوبي اتقفل", "الجولة بدأت بالفعل.")
        if self.extensions_left <= 0:
            return await self._private_notice(
                interaction,
                "وصلنا للحد الأقصى",
                "استخدمت كل تمديدات الوقت المتاحة. ابدأ الجولة أو استنى العدّ التنازلي.",
            )
        self.extensions_left -= 1
        self.time_left += 10
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

    async def _publish_result_receipt(
        self,
        message,
        view,
        *,
        title: str,
        description: str,
        outcome: str,
        context: str,
    ) -> bool:
        """Send a separate receipt, falling back to the closed round board on failure."""
        try:
            await self.ctx.send(
                view=StatusPanel(
                    title,
                    description,
                    outcome,
                    developer_credit=context.startswith("final_"),
                )
            )
            return True
        except Exception:
            logger.exception(
                "Could not publish separate roulette receipt context=%s channel_id=%s",
                context,
                self.ctx.channel.id,
            )
            view._panel_text.content = f"# {title}\n{description}"
            if context.startswith("final_"):
                add_developer_credit(view)
            await try_edit_view(
                message,
                view,
                context=f"roulette_{context}_receipt_fallback",
            )
            return False

    # --- ROULETTE GAME LOOP ---
    async def run_game_loop(self):
        try:
            active_players = self.players.copy()
            round_num = 1
            currency_cog = self.ctx.bot.get_cog('Currency')
            last_round_message = None
            last_round_view = None

            while len(active_players) > 1:
                chosen_player = random.choice(active_players)
                
                img_bytes = await generate_sniper_image(chosen_player)
                file = discord.File(fp=img_bytes, filename="sniper.png")

                has_weapon = False
                if currency_cog is not None:
                    has_weapon = (
                        await asyncio.to_thread(
                            currency_cog.get_item_count,
                            chosen_player.id,
                            "double_shot",
                        )
                    ) > 0
                view = KickView(
                    chosen_player,
                    active_players,
                    has_weapon,
                    currency_cog,
                    round_num=round_num,
                )
                msg = await self.ctx.send(view=view, file=file)
                view.message = msg
                last_round_message, last_round_view = msg, view
                set_game_view(self.ctx.bot, self.ctx.channel.id, asyncio.current_task(), view)
                await wait_for_view_fixed_timeout(view, max(0.0, view.deadline - time.monotonic()))

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
                            if currency_cog and await asyncio.to_thread(
                                currency_cog.remove_item,
                                p.id,
                                "shield",
                                1,
                            ):
                                round_notes.append(
                                    f"🛡️ {p.mention} فعّل الدرع، فانعكست الرصاصة على القناص {chosen_player.mention}."
                                )
                                if not attacker_died:
                                    kicked_players.append(chosen_player)
                                    attacker_died = True
                            else:
                                # --- INSURANCE RESOLUTION ---
                                if currency_cog and await asyncio.to_thread(
                                    currency_cog.remove_item,
                                    p.id,
                                    "roulette_ins",
                                    1,
                                ):
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
                    f"# انتهى اختيار الجولة {round_num}\n"
                    "تم إغلاق الاختيار. نتيجة الجولة في الرسالة التالية."
                )
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    logger.debug("Could not finalize roulette V2 targeting panel", exc_info=True)

                outcome = (
                    "warning" if (not view.kicked_ids or kicked_players)
                    else "success"
                )
                result_description = "\n\n".join(round_notes) if round_notes else "مفيش أحداث إضافية في الجولة دي."
                await self._publish_result_receipt(
                    msg,
                    view,
                    title=f"نتيجة الجولة {round_num}",
                    description=result_description,
                    outcome=outcome,
                    context=f"round_{round_num}",
                )

                round_num += 1
                await asyncio.sleep(2)

            # --- WINNER RESOLUTION ---
            if len(active_players) == 1:
                winner = active_players[0]
                final_description = f"الناجي الوحيد والأقوى في الحفرة!\n\nالفائز النهائي هو {winner.mention}."
                if currency_cog:
                    credited, balance = await asyncio.to_thread(currency_cog.credit_balance, winner.id, 20)
                    if credited:
                        final_description += f"\n\n**رصيدك:** {balance:,} <:ckrcoin:1513743032124575784>"
                    else:
                        final_description += "\n\n⚠️ الفوز مؤكد، لكن تعذر تحديث الرصيد بسبب الحد الأقصى."
                if last_round_message is not None and last_round_view is not None:
                    await self._publish_result_receipt(
                        last_round_message,
                        last_round_view,
                        title="🏆 بطل الروليت!",
                        description=final_description,
                        outcome="success",
                        context="final_winner",
                    )
                else:
                    await self.ctx.send(
                        view=StatusPanel(
                            "🏆 بطل الروليت!",
                            final_description,
                            "success",
                            developer_credit=True,
                        )
                    )
            else:
                final_description = "الكل خرج من اللعبة؛ مفيش فائز في الجولة دي."
                if last_round_message is not None and last_round_view is not None:
                    await self._publish_result_receipt(
                        last_round_message,
                        last_round_view,
                        title="انتهت الروليت",
                        description=final_description,
                        outcome="warning",
                        context="final_no_winner",
                    )
                else:
                    await self.ctx.send(
                        view=StatusPanel(
                            "انتهت الروليت",
                            final_description,
                            "warning",
                            developer_credit=True,
                        )
                    )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Roulette game loop failed")
            await report_game_failure(
                self.ctx,
                "توقفت الروليت بسبب خطأ",
                "حصل خطأ تقني أثناء الجولة. سجّلنا التفاصيل، واتقفلت أزرار اللوحة الحالية مع الحفاظ على سجل الجولة، واتبعث إشعار منفصل بالخطأ.",
                fallback_view=self,
            )
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Roulette(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="روليت",
        aliases=["roulette", "russianroulette", "rr"],
        description="بدء لعبة الروليت الروسي",
    )
    async def start_roulette(self, ctx):
        """يبدأ لعبة الروليت"""
        if is_active_game(self.bot, ctx.channel.id):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ واحدة جديدة.",
                "warning",
            )
        view = RouletteLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Roulette lobby", view=view):
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
    await bot.add_cog(Roulette(bot))