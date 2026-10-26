import asyncio
import io
import logging
import math
import secrets
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
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    get_currency,
    lobby_embed,
    private_status,
    report_game_failure,
    send_status,
    try_edit_view,
)

logger = logging.getLogger(__name__)

FEE = 50
MAX_PLAYERS = 25
COIN = "<:ckrcoin:1513743032124575784>"
SEGMENTS = [
    ("+20", 20),
    ("-50", -50),
    ("x2", "x2"),
    ("BOX", "box"),
    ("0", 0),
    ("+40", 40),
    ("-30", -30),
    ("LOSE", -50),
]
BOX_NET_OUTCOMES = (-50, -30, -10, 10, 30)


def resolve_wheel_segment(segment_index, *, box_net=None):
    """Resolve a sector into gross payout or final loss while keeping labels honest."""
    if isinstance(segment_index, bool) or not isinstance(segment_index, int):
        raise TypeError("segment index must be an integer")
    if not 0 <= segment_index < len(SEGMENTS):
        raise ValueError("segment index is outside the wheel")

    label, outcome = SEGMENTS[segment_index]
    if outcome == "x2":
        return label, FEE * 2, None, f"\nإجمالي الصرف يساوي ضعفي رسوم الدخول؛ صافي الربح +{FEE:,} {COIN}."
    if outcome == "box":
        net = secrets.choice(BOX_NET_OUTCOMES) if box_net is None else box_net
        if isinstance(net, bool) or not isinstance(net, int) or net not in BOX_NET_OUTCOMES:
            raise ValueError("box net outcome is not an allowed reward")
        return label, FEE + net, None, f"\n🎁 الصندوق: صافي التغيير {net:+,} {COIN}."
    if outcome > 0:
        return label, FEE + outcome, None, f"\nالقيمة الموجبة على القطاع هي صافي الربح {outcome:+,} {COIN}."
    if outcome < 0:
        return label, 0, abs(outcome), ""
    return label, FEE, None, "\nتم استرداد رسوم الدخول؛ لا ربح ولا خسارة."
SEGMENT_COLORS = [
    (245, 197, 66),
    (42, 166, 164),
    (139, 30, 30),
    (46, 204, 113),
    (230, 126, 34),
    (155, 89, 182),
]


async def publish_wheel_panel(
    ctx,
    panel,
    *,
    fallback_message=None,
    file=None,
    pending_receipts=None,
):
    """Publish the normal separate result message with a visible delivery fallback."""
    pending = pending_receipts if pending_receipts is not None else []
    kwargs = {"allowed_mentions": discord.AllowedMentions.none()}
    if file is not None:
        kwargs["file"] = file

    try:
        message = await ctx.send(view=panel, **kwargs)
    except Exception:
        logger.exception(
            "Could not publish separate wheel result channel_id=%s",
            getattr(getattr(ctx, "channel", None), "id", None),
        )
        details = "\n\n".join(
            item.content
            for item in panel.walk_children()
            if isinstance(item, TextDisplay)
        ).strip()
        pending.append(details or "# نتيجة العجلة\nتعذر نشر تفاصيل النتيجة المنفصلة.")
        if fallback_message is not None:
            fallback_text = "\n\n".join(pending)
            await try_edit_view(
                fallback_message,
                StatusPanel("نتائج العجلة غير المنشورة", fallback_text[:3600], "warning"),
                context="wheel_receipt_fallback",
            )
        return None

    if pending:
        receipt_text = "\n\n".join(pending)
        try:
            await ctx.send(
                view=StatusPanel("إيصالات سابقة تعذر نشرها", receipt_text[:3600], "warning"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            logger.exception("Could not flush previously failed wheel receipts")
            if fallback_message is not None:
                await try_edit_view(
                    fallback_message,
                    StatusPanel("نتائج العجلة غير المنشورة", receipt_text[:3600], "warning"),
                    context="wheel_receipt_flush_fallback",
                )
        else:
            pending.clear()
            if fallback_message is not None:
                await try_edit_view(
                    fallback_message,
                    StatusPanel(
                        "العجلة مستمرة",
                        "تم نشر النتائج السابقة في رسالة منفصلة. تابع رسالة نتيجة كل لاعب وسجل الجولة.",
                        "neutral",
                    ),
                    context="wheel_receipt_recovered",
                )
    return message


def format_wheel_summary(results):
    """Render a concise final scoreboard without exposing wager accounting.

    Each row is (player, result_label, net_change). A missing label means the
    player could not enter; "balance_limit" means the settlement was safely
    refused. The net delta determines the result category but is not displayed.
    """
    lines = []
    for player, label, net_change in results:
        if label is None:
            lines.append(f"⛔ {player.mention} — رصيد غير كافٍ لرسوم الدخول.")
        elif label == "balance_limit":
            lines.append(f"⚠️ {player.mention} — ماقدرناش نصرف الجائزة بسبب حد الرصيد.")
        else:
            marker = "🟢" if net_change > 0 else "🔴" if net_change < 0 else "⚪"
            label_text = "مكسب" if net_change > 0 else "خسارة" if net_change < 0 else "تعادل"
            lines.append(f"{marker} {player.mention} — {label_text}")
    return "\n".join(lines) or "مفيش نتائج للجولة."

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
        rules = ""
        return lobby_embed(
            "🎡 عجلة الحفرة",
            rules,
            self.players,
            self.time_left,
            max_players=MAX_PLAYERS,
            host=self.ctx.author,
            min_players=1,
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
                    try:
                        await self.message.edit(view=self)
                    except discord.HTTPException:
                        logger.debug("Could not refresh wheel lobby", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def start_game(self):
        if self.started:
            return

        if not self.players:
            self.started = True
            self._panel_text.content = "# انتهى التسجيل\nمحدش انضم للجولة دي. افتح لوبي جديد عشان تلعبوا."
            self._disable_buttons()
            await try_edit_view(self.message, self, context="wheel_lobby_empty")
            release_game_task(self.ctx.bot, self.ctx.channel.id, self.lobby_task)
            self.stop()
            return

        self.started = True
        self._panel_text.content = "# الجولة بدأت\nاللوبي اتقفل. بنجهز نتائج العجلة."
        self._disable_buttons()
        await try_edit_view(self.message, self, context="wheel_lobby_start")

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
        if self.started:
            return await private_status(
                interaction,
                "اللوبي اتقفل",
                "الجولة بدأت بالفعل؛ ضغطة البداية التانية ما غيّرتش حالة اللعبة.",
                "warning",
            )

        await interaction.response.defer()
        if self.started:
            return await private_status(
                interaction,
                "اللوبي اتقفل",
                "الجولة بدأت بالفعل؛ ضغطة البداية التانية ما غيّرتش حالة اللعبة.",
                "warning",
            )
        self.time_left = 0
        await self.start_game()

    async def play_rounds(self):
        currency = get_currency(self.ctx.bot)
        results = []
        pending_receipts = []

        async def publish_panel(panel, file=None):
            # Normal play posts a distinct message per spin; failures are preserved
            # on the closed lobby, then re-emitted separately when sending recovers.
            return await publish_wheel_panel(
                self.ctx,
                panel,
                fallback_message=self.message,
                file=file,
                pending_receipts=pending_receipts,
            )

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
                segment_index = secrets.randbelow(len(SEGMENTS))
                label, payout, target_loss, _outcome_note = resolve_wheel_segment(segment_index)

                settlement = await asyncio.to_thread(
                    currency.settle_wheel,
                    player.id,
                    payout=payout,
                    target_loss=target_loss,
                )
                if not settlement["played"]:
                    reason = settlement["reason"]
                    if reason == "insufficient":
                        results.append((player, None, 0))
                        title = "رصيد غير كافٍ"
                        description = (
                            f"**اللاعب:** {player.mention}\n"
                            f"رصيدك أقل من رسوم الدخول ({FEE:,} {COIN}).\n"
                            "لم يتم خصم أي عملات."
                        )
                    else:
                        results.append((player, "balance_limit", 0))
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
                results.append((player, label, net_change))
                result_label = "مكسب" if net_change > 0 else "خسارة" if net_change < 0 else "تعادل"
                description = (
                    f"**اللاعب:** {player.mention}\n"
                    f"**النتيجة:** {result_label}\n"
                    f"**رصيدك:** {balance_after:,} {COIN}"
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

            await publish_panel(
                WheelResultPanel(
                    "🏁 خلصت جولات العجلة",
                    format_wheel_summary(results),
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
                        "الجولة اتوقفت بسبب خطأ تقني. سجّلنا التفاصيل، والنتائج السابقة تفضل ظاهرة كسجل للجولة.",
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
    async def wheel(self, ctx):
        if is_active_game(self.bot, ctx.channel.id):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ العجلة.",
                "warning",
            )

        view = WheelLobby(ctx)
        if not register_game_task(self.bot, ctx.channel.id, view.lobby_task, ctx.author.id, "Wheel lobby", view=view):
            view.lobby_task.cancel()
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ العجلة.",
                "warning",
            )

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
