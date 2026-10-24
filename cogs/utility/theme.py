# --- EMBED THEME ---
"""موحد شكل البوت - الوان الحفرة + helpers"""
import discord
import logging
from discord.ui import Container, LayoutView, MediaGallery, Separator, TextDisplay
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


def embed_to_v2(embed: discord.Embed) -> LayoutView:
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

    view = LayoutView(timeout=180)
    view.add_item(Container(*children, accent_color=accent))
    return view


class StatusPanel(LayoutView):
    def __init__(self, title: str, description: str, outcome: str = "neutral"):
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
        content = f"{body[:3850]}\n\n{footer}"
        self.add_item(
            Container(
                TextDisplay(content),
                Separator(),
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
    """Report an unexpected game failure by editing the live panel when possible.

    Editing the active message is preferred to avoid stacking duplicate failure
    posts into a game channel. A new panel is sent only when no usable message
    is available or Discord rejects the edit.
    """
    logger = logging.getLogger(__name__)
    panel = StatusPanel(title, description, "warning")
    active_games = getattr(ctx.bot, "active_games", {})
    entry = active_games.get(ctx.channel.id, {}) if isinstance(active_games, dict) else {}
    if not isinstance(entry, dict):
        entry = {}
    active_view = entry.get("view")
    message = (
        getattr(active_view, "message", None)
        or getattr(fallback_view, "message", None)
        or entry.get("message")
    )

    if message is not None:
        try:
            await message.edit(content=None, embeds=[], attachments=[], view=panel)
            return True
        except discord.HTTPException:
            logger.exception("Failed to replace the active game message with a failure panel")

    try:
        await ctx.send(view=panel)
        return True
    except discord.HTTPException:
        logger.exception("Failed to send a game failure panel")
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
    """Build a consistent lobby panel with host, capacity, minimum and countdown."""
    player_list = (
        "\n".join(f"**{index + 1}.** {player.mention}" for index, player in enumerate(players))
        if players
        else "مفيش لاعبين لسه. انضم للّوبي وابدأ اللعب مع المجموعة."
    )
    host_mention = getattr(host, "mention", None)
    host_name = host_mention or getattr(host, "display_name", getattr(host, "name", "مضيف اللوبي"))
    host_line = f"**المضيف:** {host_name}\n" if host is not None else ""
    minimum_line = (
        f"**الحد الأدنى للبدء:** {min_players} لاعب\n"
        if min_players > 1
        else ""
    )
    early_start_line = (
        "صاحب اللوبي يقدر يبدأ الجولة بدري؛ الأزرار بتتقفل أول ما اللعب يبدأ."
        if host is not None
        else ""
    )
    details = "\n".join(
        line for line in (host_line.rstrip("\n"), minimum_line.rstrip("\n"), early_start_line) if line
    )
    roster = f"**اللاعبون ({len(players)}/{max_players}):**\n{player_list}"
    countdown = f"**الوقت المتبقي:** {max(0, int(time_left))} ثانية"
    sections = [rules.strip(), details, roster, countdown]
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
