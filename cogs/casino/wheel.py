import asyncio
import io
import math
import random

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from cogs.casino.game_registry import (
    is_active_game,
    owns_game_task,
    register_game_task,
    release_game_task,
)
from cogs.casino.permissions import is_staff_or_owner
from cogs.utility.theme import (
    THEME,
    WatermarkView,
    base_embed,
    get_currency,
    lobby_embed,
)

FEE = 50
MAX_PLAYERS = 25
COIN = "<:ckrcoin:1513743032124575784>"
SEGMENTS = [
    ("+100", 100),
    ("-50", -50),
    ("x2", "x2"),
    ("BOX", "box"),
    ("0", 0),
    ("+150", 150),
    ("-30", -30),
    ("+70", 70),
]
SEGMENT_COLORS = [
    (245, 197, 66),
    (42, 166, 164),
    (139, 30, 30),
    (46, 204, 113),
    (230, 126, 34),
    (155, 89, 182),
]


def format_wheel_breakdown(payout, target_loss=None, refund=0):
    lines = [f"**رسوم الدخول:** −{FEE:,} {COIN}"]
    lines.append(f"**المكافأة المصروفة:** +{payout:,} {COIN}" if payout else f"**المكافأة المصروفة:** 0 {COIN}")
    if target_loss is not None:
        lines.append(f"**الخسارة الإجمالية:** −{target_loss:,} {COIN}")
    if refund:
        lines.append(f"**المردود من رسوم الدخول:** +{refund:,} {COIN}")
    return "\\n".join(lines)

# --- WHEEL ART ---
def render_wheel(highlight=None):
    size = 600
    center = size // 2
    radius = 260
    image = Image.new("RGBA", (size, size), (15, 15, 15, 255))
    draw = ImageDraw.Draw(image)
    segment_count = len(SEGMENTS)

    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    for index, (label, _) in enumerate(SEGMENTS):
        start = (360 / segment_count) * index - 90
        end = (360 / segment_count) * (index + 1) - 90
        color = (255, 255, 255) if index == highlight else SEGMENT_COLORS[index % len(SEGMENT_COLORS)]
        draw.pieslice(
            (center - radius, center - radius, center + radius, center + radius),
            start=start,
            end=end,
            fill=color + (255,),
            outline=(0, 0, 0, 255),
            width=3,
        )
        angle = math.radians((start + end) / 2)
        position = (
            center + int(math.cos(angle) * 160),
            center + int(math.sin(angle) * 160),
        )
        draw.text(position, label, fill=(0, 0, 0, 255), font=font, anchor="mm")

    draw.ellipse(
        (center - 40, center - 40, center + 40, center + 40),
        fill=(57, 58, 65, 255),
        outline=(245, 197, 66, 255),
        width=4,
    )
    draw.polygon(((center - 18, 8), (center + 18, 8), (center, 45)), fill=(255, 60, 60, 255))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# --- LOBBY VIEW ---
class WheelLobby(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=45)
        self.ctx = ctx
        self.players = []
        self.started = False
        self.time_left = 35
        self.message = None
        self.lobby_task = ctx.bot.loop.create_task(self.run_timer())

    def make_embed(self):
        rules = (
            f"**رسوم الدخول:** {FEE:,} {COIN}\n"
            "**طريقة اللعب:** لف العجلة واكسب أو اخسر حسب النتيجة.\n"
            "المبلغ الظاهر في النتيجة هو صافي التغيير في رصيدك."
        )
        return lobby_embed(
            "🎡 عجلة الحفرة",
            rules,
            self.players,
            self.time_left,
            max_players=MAX_PLAYERS,
        )

    async def run_timer(self):
        try:
            while self.time_left > 0 and not self.started:
                await asyncio.sleep(5)
                if self.started:
                    break
                self.time_left = max(0, self.time_left - 5)
                if self.time_left == 0:
                    await self.start_game()
                elif self.message:
                    await self.message.edit(embed=self.make_embed())
        except asyncio.CancelledError:
            raise

    async def start_game(self):
        if self.started:
            return

        if not self.players:
            self.started = True
            for item in self.children:
                item.disabled = True
            if self.message:
                await self.message.edit(
                    embed=base_embed(
                        title="🎡 انتهى التسجيل",
                        description="محدش انضم للجولة دي. افتح لوبي جديد عشان تلعبوا.",
                        color=THEME["orange"],
                    ),
                    view=self,
                )
            release_game_task(self.ctx.bot, self.ctx.channel.id, self.lobby_task)
            self.stop()
            return

        self.started = True
        for item in self.children:
            item.disabled = True
        if self.message:
            await self.message.edit(view=self)

        task = self.ctx.bot.loop.create_task(self.play_rounds())
        registered = register_game_task(
            self.ctx.bot,
            self.ctx.channel.id,
            task,
            self.ctx.author.id,
            "Wheel",
            replace_task=self.lobby_task,
            view=self,
        )
        if not registered:
            task.cancel()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.started:
            await interaction.response.send_message("الجولة بدأت بالفعل.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="انضمام", emoji="🎡", style=discord.ButtonStyle.secondary)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in self.players:
            return await interaction.response.send_message("أنت مسجل بالفعل.", ephemeral=True)
        if len(self.players) >= MAX_PLAYERS:
            return await interaction.response.send_message("اللوبي اكتمل.", ephemeral=True)

        self.players.append(interaction.user)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="مغادرة", style=discord.ButtonStyle.danger)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.players:
            return await interaction.response.send_message("أنت مش مسجل في الجولة.", ephemeral=True)

        self.players.remove(interaction.user)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="ابدأ الآن", style=discord.ButtonStyle.secondary)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("الهوست بس يقدر يبدأ الجولة.", ephemeral=True)

        await interaction.response.defer()
        self.time_left = 0
        await self.start_game()

    async def play_rounds(self):
        currency = get_currency(self.ctx.bot)
        results = []
        result_message = self.message

        try:
            if currency is None:
                if result_message:
                    await result_message.edit(
                        embed=base_embed(
                            title="تعذر بدء العجلة",
                            description="نظام الاقتصاد غير متاح حاليًا. حاول مرة تانية بعد إصلاحه.",
                            color=THEME["red_dark"],
                        ),
                        view=None,
                    )
                return

            players = list(self.players)
            for index, player in enumerate(players, start=1):
                balance_before = currency.get_balance(player.id)
                segment_index = random.randrange(len(SEGMENTS))
                label, outcome = SEGMENTS[segment_index]
                outcome_note = ""
                payout = 0
                target_loss = None

                if outcome == "x2":
                    payout = FEE * 2
                elif outcome == "box":
                    payout = random.choice([20, 50, 80, 120, 200])
                    outcome_note = "\\n🎁 جائزة الصندوق من المكافآت العشوائية."
                elif isinstance(outcome, int) and outcome > 0:
                    payout = outcome
                elif isinstance(outcome, int) and outcome < 0:
                    target_loss = abs(outcome)
                else:
                    target_loss = FEE

                settlement = currency.settle_wheel(
                    player.id,
                    payout=payout,
                    target_loss=target_loss,
                )
                if not settlement["played"]:
                    reason = settlement["reason"]
                    if reason == "insufficient":
                        results.append((player, None, balance_before, balance_before))
                        title = "رصيد غير كافٍ"
                        description = (
                            f"**اللاعب:** {player.mention}\\n"
                            f"رصيدك أقل من رسوم الدخول ({FEE:,} {COIN}).\\n"
                            "لم يتم خصم أي عملات."
                        )
                    else:
                        results.append((player, "balance_limit", balance_before, balance_before))
                        title = "تعذر صرف النتيجة"
                        description = (
                            f"**اللاعب:** {player.mention}\\n"
                            "رصيدك قريب من الحد الأقصى المدعوم؛ لم يتم خصم رسوم الدخول."
                        )
                    skipped_embed = base_embed(
                        title=f"🎡 نتيجة اللفة {index}/{len(players)} • {title}",
                        description=description,
                        color=THEME["orange"],
                    )
                    if result_message:
                        await result_message.edit(embed=skipped_embed, attachments=[], view=None)
                    else:
                        result_message = await self.ctx.send(embed=skipped_embed)
                    await asyncio.sleep(0.8)
                    continue

                balance_after = settlement["after"]
                net_change = settlement["net"]
                refund = settlement["refund"]
                results.append((player, label, balance_before, balance_after))

                result_embed = base_embed(
                    title=f"🎡 نتيجة اللفة {index}/{len(players)}",
                    description=(
                        f"**اللاعب:** {player.mention}\\n"
                        f"**النتيجة:** {label}{outcome_note}\\n"
                        f"{format_wheel_breakdown(payout, target_loss, refund)}\\n"
                        f"**صافي الرصيد:** {net_change:+,} {COIN}\\n"
                        f"**الرصيد الحالي:** {balance_after:,} {COIN}"
                    ),
                    color=THEME["gold"] if net_change > 0 else THEME["red_dark"] if net_change < 0 else THEME["grey"],
                )
                result_embed.set_image(url="attachment://wheel.png")
                image_file = discord.File(render_wheel(segment_index), filename="wheel.png")
                if result_message:
                    await result_message.edit(embed=result_embed, attachments=[image_file], view=None)
                else:
                    result_message = await self.ctx.send(embed=result_embed, file=image_file)
                await asyncio.sleep(1.5)
            lines = []
            for player, label, before, after in results:
                if label is None:
                    lines.append(f"⛔ {player.mention} — رصيد غير كافٍ لرسوم الدخول.")
                elif label == "balance_limit":
                    lines.append(f"⚠️ {player.mention} — ماقدرناش نصرف الجائزة بسبب حد الرصيد.")
                else:
                    net = after - before
                    lines.append(f"{'🟢' if net > 0 else '🔴' if net < 0 else '⚪'} {player.mention} — {net:+,} {COIN}")

            final_embed = base_embed(
                title="🏁 خلصت جولات العجلة",
                description="\n".join(lines) or "مفيش نتائج للجولة.",
                color=THEME["green"],
            )
            if result_message:
                await result_message.edit(embed=final_embed, attachments=[], view=WatermarkView())
            else:
                await self.ctx.send(embed=final_embed, view=WatermarkView())
        except asyncio.CancelledError:
            if result_message:
                try:
                    await result_message.edit(
                        embed=base_embed(
                            title="🛑 العجلة اتوقفت",
                            description="تم إيقاف الجولة بواسطة الستاف أو الهوست.",
                            color=THEME["orange"],
                        ),
                        view=None,
                    )
                except discord.HTTPException:
                    pass
            raise
        except Exception:
            import traceback
            traceback.print_exc()
            if result_message:
                try:
                    await result_message.edit(
                        embed=base_embed(
                            title="حصل خطأ في العجلة",
                            description="الجولة اتوقفت بسبب خطأ غير متوقع. راجع سجل البوت.",
                            color=THEME["red_dark"],
                        ),
                        view=None,
                    )
                except discord.HTTPException:
                    pass
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Wheel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="عجلة", aliases=["عجله", "wheel"], description="ابدأ لعبة عجلة الحفرة")
    @is_staff_or_owner()
    async def wheel(self, ctx):
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة أو لوبي شغال بالفعل.", color=THEME["red_dark"]))

        view = WheelLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.lobby_task, ctx.author.id, "Wheel lobby", view=view):
            view.lobby_task.cancel()
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة أو لوبي شغال بالفعل.", color=THEME["red_dark"]))

        try:
            view.message = await ctx.send(embed=view.make_embed(), view=view)
        except Exception:
            release_game_task(self.bot, ctx.channel.id, view.lobby_task)
            view.lobby_task.cancel()
            raise

        if not owns_game_task(self.bot, ctx.channel.id, view.lobby_task):
            for item in view.children:
                item.disabled = True
            await view.message.edit(view=view)


async def setup(bot):
    await bot.add_cog(Wheel(bot))
