import asyncio
import io
import logging
import math
import random
import config
from functools import partial

import discord
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from cogs.casino.game_registry import (
    is_active_game,
    owns_game_task,
    register_game_task,
    release_game_task,
)
from cogs.casino.permissions import is_staff_or_admin
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    WatermarkView,
    base_embed,
    get_currency,
    lobby_embed,
    report_game_failure,
)

logger = logging.getLogger(__name__)

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
    """Render mutually exclusive accounting paths with no double-counting."""
    if target_loss is not None:
        refund_note = (
            f" (اترد {refund:,} من رسوم الدخول بالفعل)"
            if refund
            else ""
        )
        return (
            f"**الخسارة النهائية شاملة رسوم الدخول:** "
            f"−{target_loss:,} {COIN}{refund_note}"
        )

    return "\n".join(
        [
            f"**رسوم الدخول:** −{FEE:,} {COIN}",
            (
                f"**المكافأة المصروفة:** +{payout:,} {COIN}"
                if payout
                else f"**المكافأة المصروفة:** 0 {COIN}"
            ),
        ]
    )


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
class WheelResultPanel(LayoutView):
    def __init__(self, title, description, outcome="neutral", image_name=None):
        super().__init__(timeout=120)
        accents = {
            "success": THEME["green"],
            "win": THEME["gold"],
            "warning": THEME["orange"],
            "loss": THEME["red_dark"],
            "neutral": THEME["grey"],
        }
        self._panel_text = TextDisplay(
            f"# {title}\n{description}\n\n-# عجلة الحفرة • {config.BRAND}"
        )
        content = [self._panel_text]
        if image_name:
            content.extend([
                Separator(),
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=f"attachment://{image_name}",
                        description="نتيجة العجلة",
                    )
                ),
            ])
        self.add_item(Container(*content, accent_color=accents.get(outcome, THEME["grey"])))


class WheelLobby(LayoutView):
    def __init__(self, ctx):
        super().__init__(timeout=45)
        self.ctx = ctx
        self.players = []
        self.started = False
        self.time_left = 35
        self.message = None
        self._panel_text = TextDisplay(self._panel_content())
        self._buttons = []
        for label, custom_id, callback_name, emoji in (
            ("انضمام", "wheel_join", "join_button", "🎡"),
            ("مغادرة", "wheel_leave", "leave_button", None),
            ("ابدأ الآن", "wheel_start", "start_button", None),
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

    def _panel_content(self, embed=None):
        embed = embed or self.make_embed()
        title = f"# {embed.title}\n" if embed.title else ""
        return (title + f"{embed.description or ''}\n\n-# كازينو الحفرة • {config.BRAND}")[:4000]

    def _refresh_panel(self, embed=None):
        self._panel_text.content = self._panel_content(embed)

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    async def _private_notice(self, interaction, message):
        await interaction.response.send_message(
            view=StatusPanel("تنبيه", message, "warning"),
            ephemeral=True,
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
                    self._refresh_panel()
                    await self.message.edit(view=self)
        except asyncio.CancelledError:
            raise

    async def start_game(self):
        if self.started:
            return

        if not self.players:
            self.started = True
            self._panel_text.content = "# انتهى التسجيل\nمحدش انضم للجولة دي. افتح لوبي جديد عشان تلعبوا."
            self._disable_buttons()
            if self.message:
                await self.message.edit(view=self)
            release_game_task(self.ctx.bot, self.ctx.channel.id, self.lobby_task)
            self.stop()
            return

        self.started = True
        self._panel_text.content = "# الجولة بدأت\nاللوبي اتقفل. بنجهز نتائج العجلة."
        self._disable_buttons()
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
            await self._private_notice(interaction, "الجولة بدأت بالفعل.")
            return False
        return True

    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in self.players:
            return await self._private_notice(interaction, "أنت مسجل بالفعل.")
        if len(self.players) >= MAX_PLAYERS:
            return await self._private_notice(interaction, "اللوبي اكتمل.")

        self.players.append(interaction.user)
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.players:
            return await self._private_notice(interaction, "أنت مش مسجل في الجولة.")

        self.players.remove(interaction.user)
        self._refresh_panel()
        await interaction.response.edit_message(view=self)

    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await self._private_notice(interaction, "الهوست بس يقدر يبدأ الجولة.")

        await interaction.response.defer()
        self.time_left = 0
        await self.start_game()

    async def play_rounds(self):
        currency = get_currency(self.ctx.bot)
        results = []
        result_message = self.message

        async def publish_panel(panel, file=None):
            nonlocal result_message
            attachments = [file] if file is not None else []
            if result_message is not None:
                await result_message.edit(view=panel, attachments=attachments)
            else:
                kwargs = {"file": file} if file is not None else {}
                result_message = await self.ctx.send(view=panel, **kwargs)

        try:
            if currency is None:
                await publish_panel(
                    StatusPanel(
                        "تعذر بدء العجلة",
                        "نظام الاقتصاد غير متاح حاليًا. حاول تاني بعد إصلاحه.",
                        "warning",
                    )
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
                    outcome_note = "\n🎁 جائزة الصندوق من المكافآت العشوائية."
                elif isinstance(outcome, int) and outcome > 0:
                    payout = outcome
                elif isinstance(outcome, int) and outcome < 0:
                    target_loss = abs(outcome)
                else:
                    target_loss = FEE
                    if outcome == 0:
                        outcome_note = "\nلم تكسب جائزة؛ الخسارة النهائية هي رسوم الدخول."

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
                            f"**اللاعب:** {player.mention}\n"
                            f"رصيدك أقل من رسوم الدخول ({FEE:,} {COIN}).\n"
                            "لم يتم خصم أي عملات."
                        )
                    else:
                        results.append((player, "balance_limit", balance_before, balance_before))
                        title = "تعذر صرف النتيجة"
                        description = (
                            f"**اللاعب:** {player.mention}\n"
                            "رصيدك قريب من الحد الأقصى المدعوم؛ لم يتم خصم رسوم الدخول."
                        )
                    panel = WheelResultPanel(
                        f"🎡 نتيجة اللفة {index}/{len(players)} • {title}",
                        description,
                        outcome="warning",
                    )
                    await publish_panel(panel)
                    await asyncio.sleep(0.8)
                    continue

                balance_after = settlement["after"]
                net_change = settlement["net"]
                refund = settlement["refund"]
                results.append((player, label, balance_before, balance_after))
                description = (
                    f"**اللاعب:** {player.mention}\n"
                    f"**النتيجة:** {label}{outcome_note}\n"
                    f"{format_wheel_breakdown(payout, target_loss, refund)}\n"
                    f"**صافي الرصيد:** {net_change:+,} {COIN}\n"
                    f"**الرصيد الحالي:** {balance_after:,} {COIN}"
                )
                outcome_color = "win" if net_change > 0 else "loss" if net_change < 0 else "neutral"
                panel = WheelResultPanel(
                    f"🎡 نتيجة اللفة {index}/{len(players)}",
                    description,
                    outcome=outcome_color,
                    image_name="wheel.png",
                )
                image_file = discord.File(render_wheel(segment_index), filename="wheel.png")
                await publish_panel(panel, image_file)
                await asyncio.sleep(1.5)

            lines = []
            for player, label, before, after in results:
                if label is None:
                    lines.append(f"⛔ {player.mention} — رصيد غير كافٍ لرسوم الدخول.")
                elif label == "balance_limit":
                    lines.append(f"⚠️ {player.mention} — ماقدرناش نصرف الجائزة بسبب حد الرصيد.")
                else:
                    net = after - before
                    marker = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
                    lines.append(f"{marker} {player.mention} — {net:+,} {COIN}")

            await publish_panel(
                WheelResultPanel(
                    "🏁 خلصت جولات العجلة",
                    "\n".join(lines) or "مفيش نتائج للجولة.",
                    outcome="success",
                )
            )
        except asyncio.CancelledError:
            try:
                await publish_panel(
                    StatusPanel(
                        "العجلة اتوقفت",
                        "تم إيقاف الجولة بواسطة الستاف أو الهوست.",
                        "warning",
                    )
                )
            except discord.HTTPException:
                logger.debug("Could not update the cancelled wheel panel", exc_info=True)
            raise
        except Exception:
            logger.exception("Wheel game loop failed")
            try:
                await publish_panel(
                    StatusPanel(
                        "حصل خطأ في العجلة",
                        "الجولة اتوقفت بسبب خطأ تقني. سجّلنا التفاصيل من غير تكرار رسائل جديدة.",
                        "warning",
                    )
                )
            except discord.HTTPException:
                logger.exception("Could not publish the wheel failure panel")
            return
        finally:
            release_game_task(self.ctx.bot, self.ctx.channel.id, asyncio.current_task())


# --- COG COMMANDS ---
class Wheel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="عجلة", aliases=["عجله", "wheel"], description="ابدأ لعبة عجلة الحفرة")
    @is_staff_or_admin()
    async def wheel(self, ctx):
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة أو لوبي شغال بالفعل.", color=THEME["red_dark"]))

        view = WheelLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.lobby_task, ctx.author.id, "Wheel lobby", view=view):
            view.lobby_task.cancel()
            return await ctx.send(embed=base_embed(description="❌ فيه لعبة أو لوبي شغال بالفعل.", color=THEME["red_dark"]))

        try:
            view.message = await ctx.send(view=view)
        except Exception:
            release_game_task(self.bot, ctx.channel.id, view.lobby_task)
            view.lobby_task.cancel()
            raise

        if not owns_game_task(self.bot, ctx.channel.id, view.lobby_task):
            view._disable_buttons()
            await view.message.edit(view=view)


async def setup(bot):
    await bot.add_cog(Wheel(bot))
