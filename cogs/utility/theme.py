"""موحد شكل البوت - الوان الحفرة + helpers"""
import asyncio
import discord
import logging
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay
import config

THEME = {
    "gold": 0xF5C542,      # فوز / تتويج
    "red_dark": 0x8B1E1E,  # خطر / طرد / فخ
    "teal": 0x2AA6A4,      # لوبي / انتظار
    "grey": 0x393A41,      # خلفية / محايد
    "green": 0x2ECC71,     # صح / كسب
    "orange": 0xE67E22,    # تحذير / ضريبة
}

LOGO_URL = getattr(config, "SERVER_LOGO_URL", "")
BRAND = getattr(config, "BRAND", "Çukur")
PREFIX = getattr(config, "PREFIX", "+")


def base_embed(title: str = None, description: str = None, color: int = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color or THEME["grey"])
    footer_text = f"كازينو الحفرة • {BRAND}"
    if LOGO_URL:
        e.set_thumbnail(url=LOGO_URL)
        e.set_footer(text=footer_text, icon_url=LOGO_URL)
    else:
        e.set_footer(text=footer_text)
    return e


def developer_credit_row() -> ActionRow:
    """Return the disabled, non-interactive developer credit used on final game results."""
    return ActionRow(
        discord.ui.Button(
            label="Developed by Sayler & Neglawy",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
    )


def add_developer_credit(view: LayoutView) -> LayoutView:
    """Attach the standard credit to a completed game view, safely and at most once."""
    if not isinstance(view, LayoutView) or getattr(view, "_developer_credit_added", False):
        return view
    view.add_item(Container(developer_credit_row(), accent_color=THEME["grey"]))
    view._developer_credit_added = True
    return view


def embed_to_v2(embed: discord.Embed, *, developer_credit: bool = False) -> LayoutView:
    """Convert a legacy informational embed into a Components V2 panel.

    This preserves the title, body, fields, footer, accent color, and meaningful
    artwork while keeping every announcement as its own message/event. The
    server-logo thumbnail used by base_embed is intentionally omitted so it
    doesn't become a large, repetitive gallery image.
    """
    sections = []
    author_name = getattr(getattr(embed, "author", None), "name", None)
    if author_name:
        sections.append(f"**{author_name}**")
    if embed.title:
        sections.append(f"# {embed.title}")
    if embed.description:
        sections.append(embed.description)
    for field in getattr(embed, "fields", ()):
        name = getattr(field, "name", "") or "تفاصيل"
        value = getattr(field, "value", "") or "—"
        sections.append(f"**{name}**\n{value}")
    footer_text = getattr(getattr(embed, "footer", None), "text", None)
    if footer_text:
        sections.append(f"-# {footer_text}")

    body = "\n\n".join(section for section in sections if section).strip()
    if not body:
        body = "# إشعار"
    if len(body) > 3900:
        body = body[:3897] + "…"

    color = getattr(embed, "color", None)
    accent = color.value if color is not None else THEME["grey"]
    children = [TextDisplay(body)]

    image_url = getattr(getattr(embed, "image", None), "url", None)
    thumbnail_url = getattr(getattr(embed, "thumbnail", None), "url", None)
    if not image_url and thumbnail_url and thumbnail_url != LOGO_URL:
        image_url = thumbnail_url

    if image_url:
        children.extend(
            [
                Separator(),
                MediaGallery(
                    discord.MediaGalleryItem(
                        media=image_url,
                        description=(embed.title or "صورة مرفقة")[:256],
                    )
                ),
            ]
        )
    if developer_credit:
        children.extend([Separator(), developer_credit_row()])

    view = LayoutView(timeout=180)
    view.add_item(Container(*children, accent_color=accent))
    return view


class StatusPanel(LayoutView):
    def __init__(
        self,
        title: str,
        description: str,
        outcome: str = "neutral",
        *,
        developer_credit: bool = False,
    ):
        super().__init__(timeout=120)
        colors = {
            "win": THEME["gold"],
            "success": THEME["green"],
            "loss": THEME["red_dark"],
            "warning": THEME["orange"],
            "neutral": THEME["grey"],
        }
        body = f"# {title}\n{description}" if description else f"# {title}"
        footer = f"-# كازينو الحفرة • {BRAND}"
        children = [TextDisplay(body[:3850]), Separator(), TextDisplay(footer)]
        if developer_credit:
            children.extend([Separator(), developer_credit_row()])
        self.add_item(
            Container(
                *children,
                accent_color=colors.get(outcome, THEME["grey"]),
            )
        )


async def send_status(ctx, title: str, description: str, outcome: str = "neutral"):
    """Send a branded Components V2 status panel from a prefix or slash command.

    Slash-command errors are ephemeral to avoid polluting busy game channels;
    prefix-command responses remain visible in the channel where the command ran.
    """
    kwargs = {"ephemeral": True} if getattr(ctx, "interaction", None) is not None else {}
    return await ctx.send(view=StatusPanel(title, description, outcome), **kwargs)


async def private_status(interaction, title: str, description: str, outcome: str = "warning"):
    """Acknowledge an interaction with the standard private Components V2 status panel."""
    panel = StatusPanel(title, description, outcome)
    if interaction.response.is_done():
        return await interaction.followup.send(view=panel, ephemeral=True)
    return await interaction.response.send_message(view=panel, ephemeral=True)


async def wait_for_view_fixed_timeout(view, timeout: float) -> bool:
    """Wait for a fixed wall-clock deadline even when component clicks reset View.timeout.

    Returns True when the deadline expires and False when the view completes naturally.
    Callers should disable controls and render an appropriate closed-state panel afterward.
    """
    waiter = asyncio.create_task(view.wait())
    try:
        done, _pending = await asyncio.wait({waiter}, timeout=max(0.0, float(timeout)))
        if waiter in done:
            return bool(await waiter)

        # Keep gameplay deadlines fixed.
        view.stop()
        await waiter
        return True
    except BaseException:
        if not waiter.done():
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass
        raise


async def try_edit_view(message, view, *, context: str) -> bool:
    """Best-effort lobby update that never lets an HTTP edit failure strand a game.

    A missing/deleted lobby message should not prevent the actual round from
    starting or prevent an empty lobby from releasing its channel reservation.
    The reason is logged locally; exception details are never sent to Discord.
    """
    if message is None:
        return False
    try:
        await message.edit(view=view)
    except discord.HTTPException:
        logging.getLogger("casino.ui").warning(
            "Could not update a game lobby panel context=%s",
            context,
            exc_info=True,
        )
        return False
    return True


async def report_game_failure(ctx, title: str, description: str, fallback_view=None) -> bool:
    """Close active controls and publish a separate V2 failure receipt.

    The original round/table remains visible as game history; only its controls are
    disabled. Repeated error paths for the same registered game are de-duplicated.
    """
    logger = logging.getLogger(__name__)
    panel = StatusPanel(title, description, "warning")
    active_games = getattr(ctx.bot, "active_games", {})
    entry = active_games.get(ctx.channel.id, {}) if isinstance(active_games, dict) else {}
    if not isinstance(entry, dict):
        entry = {}

    if entry.get("failure_reported"):
        return False
    # Reserve one failure receipt.
    # Prevent duplicate game warnings.
    entry["failure_reported"] = "pending"

    active_view = entry.get("view") or fallback_view
    message = (
        getattr(active_view, "message", None)
        or entry.get("message")
    )

    if active_view is not None:
        try:
            disable = getattr(active_view, "_disable_buttons", None)
            if callable(disable):
                disable()
            else:
                for item in active_view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True
            stop = getattr(active_view, "stop", None)
            if callable(stop):
                stop()
            if message is not None:
                await message.edit(view=active_view)
        except discord.HTTPException:
            logger.warning(
                "Could not disable failed game controls; preserving the separate failure receipt",
                exc_info=True,
            )
        except Exception:
            logger.exception("Unexpected failure while closing broken game controls")

    try:
        await ctx.send(
            view=panel,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        entry["failure_reported"] = True
        return True
    except discord.HTTPException:
        entry.pop("failure_reported", None)
        logger.exception("Failed to send a separate game failure receipt")
        return False


def result_embed(
    title: str,
    description: str,
    outcome: str = "neutral",
    thumbnail_url: str = None,
) -> discord.Embed:
    colors = {
        "win": THEME["gold"],
        "success": THEME["green"],
        "loss": THEME["red_dark"],
        "warning": THEME["orange"],
        "neutral": THEME["grey"],
    }
    embed = base_embed(title=title, description=description, color=colors.get(outcome, THEME["grey"]))
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return embed

def lobby_embed(
    title: str,
    rules: str,
    players,
    time_left: int,
    max_players: int = 25,
    *,
    host=None,
    min_players: int = 2,
) -> discord.Embed:
    """Build a compact, consistent lobby card with only the information needed to join."""
    members = list(players or ())
    visible_members = members[:5]
    player_lines = [
        f"**{index + 1}.** {player.mention}"
        for index, player in enumerate(visible_members)
    ]
    remaining_members = len(members) - len(visible_members)
    if remaining_members:
        player_lines.append(f"-# +{remaining_members} لاعب آخر")

    if player_lines:
        player_list = "\n".join(player_lines)
    else:
        player_list = "مفيش لاعبين لسه."

    metadata = []
    if host is not None:
        host_name = (
            getattr(host, "mention", None)
            or getattr(host, "display_name", None)
            or getattr(host, "name", "مضيف اللوبي")
        )
        metadata.append(f"**المضيف:** {host_name}")
    if min_players > 1:
        metadata.append(f"**الحد الأدنى للبدء:** {min_players} لاعب")

    # Keep rules concise.
    rules_text = " ".join(line.strip() for line in (rules or "").splitlines() if line.strip())
    roster = f"**اللاعبون ({len(members)}/{max_players}):**\n{player_list}"
    countdown = f"⏳ يبدأ تلقائيًا خلال **{max(0, int(time_left))} ثانية**"
    sections = [rules_text, " · ".join(metadata), roster, countdown]
    description = "\n\n".join(section for section in sections if section)
    e = discord.Embed(title=title, description=description, color=THEME["teal"])
    if LOGO_URL:
        e.set_thumbnail(url=LOGO_URL)
        e.set_footer(text=f"كازينو الحفرة • {BRAND}", icon_url=LOGO_URL)
    return e

def is_staff_or_admin():
    from cogs.casino.permissions import is_staff_or_admin as check
    return check()


def get_currency(bot):
    try:
        return bot.get_cog("Currency")
    except Exception:
        return None
