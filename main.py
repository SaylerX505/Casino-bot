# --- BOT ENTRY POINT ---
import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.casino.permissions import (
    GAME_LABELS,
    GameChannelBlocked,
    configured_log_channel_id,
    game_key_from_command_name,
    game_key_from_interaction,
    is_game_channel_allowed,
)
from cogs.utility.diagnostics import (
    install_asyncio_exception_handler,
    log_unhandled_exception,
    new_incident_id,
)
from cogs.utility.theme import StatusPanel, embed_to_v2

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# --- BOT CONFIGURATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.reactions = True
intents.guilds = True

class HomeGuildCommandTree(app_commands.CommandTree):
    """Apply single-server and optional per-game channel restrictions."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        home_guild_id = getattr(config, "HOME_GUILD_ID", None)
        if home_guild_id is not None and interaction.guild_id != home_guild_id:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "البوت ده مخصص لسيرفر واحد ومش متاح خارج السيرفر الأساسي.",
                    ephemeral=True,
                )
            return False

        game_key = game_key_from_interaction(interaction)
        if game_key and not is_game_channel_allowed(
            interaction.guild_id,
            interaction.channel_id,
            game_key,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"لعبة **{GAME_LABELS[game_key]}** متاحة في قنوات محددة بس. "
                    "راجع الستاف أو جرّب القناة المخصصة.",
                    ephemeral=True,
                )
            return False
        return True


bot = commands.Bot(
    command_prefix=config.PREFIX,
    intents=intents,
    case_insensitive=True,
    owner_ids=config.OWNER_IDS,
    tree_cls=HomeGuildCommandTree,
)
bot.remove_command('help')

# --- EVENT HANDLERS ---
ERROR_LOG_CHANNEL_ID = config.LOG_CHANNELS.get("command_errors", 0)


async def _send_command_error(ctx, message):
    """Send a styled, safe error panel without double-acknowledging interactions."""
    view = StatusPanel("تعذر تنفيذ الأمر", message, "warning")
    interaction = getattr(ctx, "interaction", None)
    if interaction is None:
        await ctx.send(view=view)
    elif interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)

@bot.check
async def enforce_home_guild_for_prefix(ctx):
    home_guild_id = getattr(config, "HOME_GUILD_ID", None)
    return home_guild_id is None or getattr(ctx.guild, "id", None) == home_guild_id


@bot.check
async def enforce_game_channel_for_prefix(ctx):
    game_key = game_key_from_command_name(getattr(ctx.command, "name", None))
    if game_key and not is_game_channel_allowed(
        getattr(ctx.guild, "id", None),
        getattr(ctx.channel, "id", None),
        game_key,
    ):
        raise GameChannelBlocked(
            f"{game_key} is restricted to configured game channels."
        )
    return True


@bot.event
async def on_guild_join(guild):
    home_guild_id = getattr(config, "HOME_GUILD_ID", None)
    if home_guild_id is not None and guild.id != home_guild_id:
        logging.getLogger("casino.guilds").warning(
            "Leaving unauthorized guild id=%s; this bot is configured for guild id=%s",
            guild.id,
            home_guild_id,
        )
        await guild.leave()


@bot.event
async def on_ready():
    logger = logging.getLogger("casino.startup")
    home_guild_id = getattr(config, "HOME_GUILD_ID", None)
    if home_guild_id is not None:
        for joined_guild in tuple(bot.guilds):
            if joined_guild.id != home_guild_id:
                logger.warning(
                    "Leaving previously joined unauthorized guild id=%s",
                    joined_guild.id,
                )
                try:
                    await joined_guild.leave()
                except discord.HTTPException:
                    logger.exception("Could not leave unauthorized guild id=%s", joined_guild.id)
    else:
        logger.warning("HOME_GUILD_ID is not configured; single-server enforcement is disabled")

    logger.info("Bot is online as user_id=%s", getattr(bot.user, "id", "unknown"))
    try:
        await bot.change_presence(status=discord.Status.dnd, activity=discord.CustomActivity(name="مرحبا المانيا, انا الحفرة"))
    except discord.HTTPException:
        logger.exception("Could not set bot presence")
    
    if not getattr(bot, "_slash_commands_synced", False):
        try:
            if config.HOME_GUILD_ID:
                guild = discord.Object(id=config.HOME_GUILD_ID)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                # Remove the global command surface so new command registrations stay
                # scoped to the one configured server.
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync()
                print(f"✅ Synced {len(synced)} server-only slash command(s)")
            else:
                synced = await bot.tree.sync()
                print(f"✅ Synced {len(synced)} global slash command(s)")
            bot._slash_commands_synced = True
        except Exception:
            logging.getLogger("casino.startup").exception("Failed to sync slash commands")

async def _forward_error_to_guild_log(
    command_name: str,
    user_id: int | str,
    guild_id: int | None,
    channel_id: int | None,
    error_type: str,
    incident_id: str = "unavailable",
) -> None:
    """Send redacted, correlated diagnostics to a configured channel in the same guild only."""
    if guild_id is None:
        return

    log_channel_id = configured_log_channel_id(
        guild_id,
        "command_errors",
        ERROR_LOG_CHANNEL_ID,
    )
    if not log_channel_id:
        return

    logger = logging.getLogger("casino.error_forwarding")
    channel = bot.get_channel(log_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(log_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Could not resolve command-error channel incident_id=%s guild_id=%s channel_id=%s",
                incident_id,
                guild_id,
                log_channel_id,
            )
            return

    channel_guild_id = getattr(getattr(channel, "guild", None), "id", None)
    if channel_guild_id != guild_id:
        logger.error(
            "Skipped cross-guild error log incident_id=%s destination_guild=%s source_guild=%s",
            incident_id,
            channel_guild_id,
            guild_id,
        )
        return

    embed = discord.Embed(
        title=f"Command Error · {incident_id}",
        description=(
            f"**رقم المتابعة:** {incident_id}\n"
            f"**الأمر:** {command_name}\n"
            f"**معرّف المستخدم:** {user_id}\n"
            f"**معرّف السيرفر:** {guild_id}\n"
            f"**معرّف الروم:** {channel_id}\n"
            f"**نوع الخطأ:** {error_type}"
        ),
        color=0x8B1E1E,
    )
    try:
        await channel.send(
            view=embed_to_v2(embed),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        logger.warning(
            "Could not send command-error log incident_id=%s guild_id=%s channel_id=%s",
            incident_id,
            guild_id,
            log_channel_id,
            exc_info=True,
        )

@bot.event
async def on_command_error(ctx, error):
    """Handle expected user errors quietly; correlate and report unexpected failures."""
    logger = logging.getLogger("casino.commands")
    command = getattr(ctx, "command", None)
    command_name = getattr(command, "qualified_name", None) or "unknown"
    user_id = getattr(getattr(ctx, "author", None), "id", None)
    guild_id = getattr(getattr(ctx, "guild", None), "id", None)
    channel_id = getattr(getattr(ctx, "channel", None), "id", None)

    if isinstance(error, commands.CommandNotFound):
        logger.debug("Ignored unknown prefix command user_id=%s guild_id=%s channel_id=%s", user_id, guild_id, channel_id)
        return

    if isinstance(error, GameChannelBlocked):
        game_key = game_key_from_command_name(getattr(command, "name", None))
        game_label = GAME_LABELS.get(game_key, "اللعبة")
        logger.warning("Channel policy rejected command=%s user_id=%s guild_id=%s channel_id=%s",
                       command_name, user_id, guild_id, channel_id)
        try:
            await _send_command_error(ctx, f"{game_label} متاحة في قنوات محددة بس. راجع الستاف أو جرّب القناة المخصصة.")
        except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
            logger.debug("Could not acknowledge channel-policy rejection", exc_info=True)
        return

    if isinstance(error, commands.CommandOnCooldown):
        logger.info("Command cooldown command=%s user_id=%s guild_id=%s retry_after=%.2f",
                    command_name, user_id, guild_id, error.retry_after)
        try:
            await _send_command_error(ctx, f"استنى {error.retry_after:.0f} ثانية قبل ما تستخدم الأمر تاني.")
        except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
            logger.debug("Could not acknowledge command cooldown", exc_info=True)
        return

    if isinstance(error, commands.UserInputError):
        logger.debug("Invalid command input command=%s user_id=%s guild_id=%s error_type=%s",
                     command_name, user_id, guild_id, type(error).__name__)
        try:
            await _send_command_error(ctx, "صيغة الأمر ناقصة أو فيها قيمة غير صحيحة. راجع الأمر من /commands وتأكد من القيم المطلوبة.")
        except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
            logger.debug("Could not acknowledge invalid command input", exc_info=True)
        return

    if isinstance(error, commands.CheckFailure):
        logger.warning("Command permission/check rejected command=%s user_id=%s guild_id=%s error_type=%s",
                       command_name, user_id, guild_id, type(error).__name__)
        try:
            await _send_command_error(ctx, "الأمر ده مش متاح لصلاحيات حسابك أو في المكان الحالي.")
        except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
            logger.debug("Could not acknowledge command check rejection", exc_info=True)
        return

    incident_id = new_incident_id()
    original_error = log_unhandled_exception(
        logger,
        incident_id=incident_id,
        surface="prefix",
        command_name=command_name,
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        error=error,
    )
    try:
        await _forward_error_to_guild_log(
            command_name,
            user_id if user_id is not None else "unknown",
            guild_id,
            channel_id,
            type(original_error).__name__,
            incident_id,
        )
    except Exception:
        logging.getLogger("casino.error_forwarding").exception(
            "Failed to forward command error incident_id=%s", incident_id
        )

    try:
        await _send_command_error(
            ctx,
            f"حصل عطل أثناء تنفيذ الأمر. جرّب تاني بعد شوية، ولو تكرر ابعت للستاف رقم المتابعة {incident_id}.",
        )
    except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
        logger.warning("Could not acknowledge failed prefix command incident_id=%s", incident_id, exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction, error):
    """Acknowledge slash-command errors once, with correlated diagnostics for real faults."""
    logger = logging.getLogger("casino.app_commands")
    command_name = (
        interaction.command.qualified_name
        if getattr(interaction, "command", None) is not None
        else "unknown"
    )
    user_id = getattr(getattr(interaction, "user", None), "id", "unknown")
    guild_id = getattr(interaction, "guild_id", None)
    channel_id = getattr(getattr(interaction, "channel", None), "id", None)

    if isinstance(error, app_commands.CommandOnCooldown):
        logger.info("App command cooldown command=%s user_id=%s guild_id=%s retry_after=%.2f",
                    command_name, user_id, guild_id, error.retry_after)
        message = f"استنى {error.retry_after:.0f} ثانية قبل ما تستخدم الأمر تاني."
    elif isinstance(error, app_commands.CheckFailure):
        logger.warning("App command check rejected command=%s user_id=%s guild_id=%s error_type=%s",
                       command_name, user_id, guild_id, type(error).__name__)
        message = "الأمر ده مش متاح لصلاحيات حسابك أو في المكان الحالي."
    elif isinstance(error, app_commands.TransformerError):
        logger.info("App command input rejected command=%s user_id=%s guild_id=%s option=%s",
                    command_name, user_id, guild_id, getattr(error, "param", None))
        message = "فيه قيمة غير صحيحة في الاختيارات. راجع صيغة الأمر وجرب تاني."
    else:
        incident_id = new_incident_id()
        original_error = log_unhandled_exception(
            logger,
            incident_id=incident_id,
            surface="slash",
            command_name=command_name,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            error=error,
        )
        try:
            await _forward_error_to_guild_log(
                command_name,
                user_id,
                guild_id,
                channel_id,
                type(original_error).__name__,
                incident_id,
            )
        except Exception:
            logging.getLogger("casino.error_forwarding").exception(
                "Failed to forward app-command error incident_id=%s", incident_id
            )
        message = f"حصل عطل أثناء تنفيذ الأمر. تم تسجيله؛ رقم المتابعة {incident_id}."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (discord.HTTPException, discord.InteractionResponded, discord.NotFound):
        logger.warning("Could not acknowledge app-command error command=%s user_id=%s guild_id=%s",
                       command_name, user_id, guild_id, exc_info=True)


@bot.event
async def on_error(event_method, *args, **kwargs):
    """Capture exceptions escaping ordinary Discord event callbacks."""
    incident_id = new_incident_id()
    logging.getLogger("casino.events").exception(
        "Uncaught event failure incident_id=%s event=%s", incident_id, event_method
    )

# --- EXTENSION LOADER ---
NON_COG_MODULES = frozenset({
    "bank.py",
    "bank_ledger.py",
    "bank_schema.py",
    "permissions.py",
    "game_registry.py",
    "game_assets.py",
    "currency_catalog.py",
    "currency_views.py",
    "geoguessr_art.py",
    "geoguessr_storage.py",
    "geoguessr_views.py",
    "mafia_art.py",
    "mafia_views.py",
    "roulette_art.py",
    "roulette_views.py",
})
UTILITY_EXTENSION_ALLOWLIST = frozenset({"help.py", "ai.py", "admin.py", "wizard.py"})


def should_load_extension(directory: str, filename: str) -> bool:
    """Return whether a Python file is a Discord extension rather than a helper module."""
    if not filename.endswith(".py") or filename.startswith("__"):
        return False
    if directory == "cogs.casino":
        return filename not in NON_COG_MODULES
    if directory == "cogs.utility":
        return filename in UTILITY_EXTENSION_ALLOWLIST
    return False


async def load_cogs():
    """Automatically loads all python files inside the cogs subfolders"""
    import pathlib
    base_dir = pathlib.Path(__file__).parent
    cog_directories = ['cogs.casino', 'cogs.utility']
    failures = []
    logger = logging.getLogger("casino.extensions")
    for directory in cog_directories:
        folder_path = base_dir / directory.replace('.', '/')
        if not folder_path.exists():
            folder_path.mkdir(parents=True, exist_ok=True)
            continue

        for filename in sorted(os.listdir(folder_path)):
            if not should_load_extension(directory, filename):
                continue

            cog_path = f"{directory}.{filename[:-3]}"
            try:
                await bot.load_extension(cog_path)
                logger.info("Loaded extension %s", cog_path)
            except Exception as e:
                failures.append(f"{cog_path}: {type(e).__name__}")
                logger.exception("Failed to load extension %s", cog_path)

    if failures:
        raise RuntimeError("Required extensions failed to load:\n" + "\n".join(failures))

# --- APPLICATION STARTUP ---
async def main():
    logger = logging.getLogger("casino.startup")
    if not config.TOKEN:
        logger.error(
            "DISCORD_TOKEN is missing. Configure the host environment using .env.example."
        )
        return
    if not config.HOME_GUILD_ID:
        logger.error(
            "HOME_GUILD_ID is required for this single-server bot. "
            "Set it to the target Discord server ID before startup."
        )
        return
    try:
        loop = asyncio.get_running_loop()
        install_asyncio_exception_handler(loop)
        async with bot:
            await load_cogs()
            await bot.start(config.TOKEN)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Bot startup/runtime failed")
        raise
    finally:
        from cogs.casino import bank
        try:
            bank.close_database()
        except Exception:
            logger.exception("Failed to close the economy database cleanly")

if __name__ == "__main__":
    asyncio.run(main())