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
from cogs.utility.theme import embed_to_v2

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
    """Use ephemeral responses only for interaction contexts, never prefix messages."""
    kwargs = {"ephemeral": True} if getattr(ctx, "interaction", None) is not None else {}
    await ctx.send(message, **kwargs)


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
) -> None:
    """Send redacted diagnostics to a configured log channel in the same guild only."""
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
                "Could not resolve command-error channel guild=%s channel=%s",
                guild_id,
                log_channel_id,
            )
            return

    channel_guild_id = getattr(getattr(channel, "guild", None), "id", None)
    if channel_guild_id != guild_id:
        logger.error(
            "Skipped command-error log because destination guild=%s does not match source guild=%s",
            channel_guild_id,
            guild_id,
        )
        return

    embed = discord.Embed(
        title="Bot Error",
        description=(
            f"**الأمر:** `{command_name}`\n"
            f"**معرّف المستخدم:** `{user_id}`\n"
            f"**معرّف السيرفر:** `{guild_id}`\n"
            f"**معرّف الروم:** `{channel_id}`\n"
            f"**نوع الخطأ:** `{error_type}`"
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
            "Could not send command-error log guild=%s channel=%s",
            guild_id,
            log_channel_id,
            exc_info=True,
        )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, GameChannelBlocked):
        game_key = game_key_from_command_name(getattr(ctx.command, "name", None))
        game_label = GAME_LABELS.get(game_key, "اللعبة")
        try:
            await _send_command_error(
                ctx,
                f"🔒 {game_label} متاحة في قنوات محددة بس. راجع الستاف أو جرّب القناة المخصصة.",
            )
        except discord.HTTPException:
            pass
        return
    if isinstance(error, commands.CheckFailure):
        try:
            await _send_command_error(ctx, "❌ الأمر ده مش متاح لصلاحيات حسابك.")
        except discord.HTTPException:
            pass
        return
    if isinstance(error, commands.CommandOnCooldown):
        try:
            await _send_command_error(
                ctx,
                f"⏳ اهدى يا معلم! جرب تاني بعد **{error.retry_after:.0f}** ثانية.",
            )
        except discord.HTTPException:
            pass
        return

    original_error = getattr(error, "original", error)
    command_text = ctx.command.qualified_name if ctx.command else "unknown"
    guild_id = getattr(ctx, "guild", None)
    guild_id = getattr(guild_id, "id", None)
    channel_id = getattr(getattr(ctx, "channel", None), "id", None)
    logger = logging.getLogger("casino.commands")
    logger.error(
        "Command failed: command=%s user_id=%s guild_id=%s channel_id=%s",
        command_text,
        getattr(getattr(ctx, "author", None), "id", None),
        guild_id,
        channel_id,
        exc_info=(type(original_error), original_error, original_error.__traceback__),
    )
    try:
        await _forward_error_to_guild_log(
            command_text,
            getattr(getattr(ctx, "author", None), "id", "unknown"),
            guild_id,
            channel_id,
            type(original_error).__name__,
        )
    except Exception:
        logger.exception("Failed to forward command error to the configured log channel")

    try:
        await _send_command_error(
            ctx,
            "❌ حصلت مشكلة! تم تسجيل الخطأ؛ جرب تاني كمان شوية.",
        )
    except discord.HTTPException:
        logger.exception("Could not send the command error acknowledgement")
@bot.tree.error
async def on_app_command_error(interaction, error):
    """Acknowledge slash-command failures safely without exposing internals."""
    logger = logging.getLogger("casino.app_commands")
    command_name = (
        interaction.command.qualified_name
        if getattr(interaction, "command", None) is not None
        else "unknown"
    )
    user_id = getattr(getattr(interaction, "user", None), "id", "unknown")
    guild_id = getattr(interaction, "guild_id", None)

    if isinstance(error, app_commands.CheckFailure):
        logger.warning(
            "Rejected app command %s for user=%s guild=%s",
            command_name,
            user_id,
            guild_id,
        )
        message = "الأمر ده مش متاح لصلاحيات حسابك."
    else:
        original = getattr(error, "original", error)
        logger.error(
            "Unhandled app command error command=%s user=%s guild=%s",
            command_name,
            user_id,
            guild_id,
            exc_info=(type(original), original, original.__traceback__),
        )
        try:
            await _forward_error_to_guild_log(
                command_name,
                user_id,
                guild_id,
                getattr(getattr(interaction, "channel", None), "id", None),
                type(original).__name__,
            )
        except Exception:
            logger.exception("Failed to forward app-command error to the configured log channel")
        message = "حصل عطل أثناء تنفيذ الأمر. تم تسجيل الخطأ من غير عرض تفاصيله للمستخدم."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Could not send the app-command error acknowledgement")


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
        async with bot:
            await load_cogs()
            await bot.start(config.TOKEN)
    finally:
        from cogs.casino import bank
        bank.close_database()

if __name__ == "__main__":
    asyncio.run(main())