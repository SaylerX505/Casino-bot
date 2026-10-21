# --- BOT ENTRY POINT ---
import discord
from discord.ext import commands
import os
import asyncio
import logging
import config
from discord import app_commands

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
    """Reject app-command interactions outside the configured single home server."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        home_guild_id = getattr(config, "HOME_GUILD_ID", None)
        if home_guild_id is None or interaction.guild_id == home_guild_id:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "البوت ده مخصص لسيرفر واحد ومش متاح خارج السيرفر الأساسي.",
                ephemeral=True,
            )
        return False


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

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
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
    # Keep remote diagnostics in the same guild as the failing command. Never
    # send raw user input or exception messages to a cross-guild log channel.
    try:
        channel = bot.get_channel(ERROR_LOG_CHANNEL_ID) if ERROR_LOG_CHANNEL_ID else None
        channel_guild_id = getattr(getattr(channel, "guild", None), "id", None)
        if channel is not None and guild_id is not None and channel_guild_id == guild_id:
            embed = discord.Embed(
                title="🚨 Bot Error",
                description=(
                    f"**الأمر:** `{command_text}`\n"
                    f"**معرّف المستخدم:** `{getattr(getattr(ctx, 'author', None), 'id', 'unknown')}`\n"
                    f"**معرّف السيرفر:** `{guild_id}`\n"
                    f"**معرّف الروم:** `{channel_id}`\n"
                    f"**نوع الخطأ:** `{type(original_error).__name__}`"
                ),
                color=0x8B1E1E,
            )
            await channel.send(content=f"<@{config.NEGLAWY_ID}>", embed=embed)
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
        message = "حصل عطل أثناء تنفيذ الأمر. تم تسجيل الخطأ من غير عرض تفاصيله للمستخدم."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Could not send the app-command error acknowledgement")


# --- EXTENSION LOADER ---
async def load_cogs():
    """Automatically loads all python files inside the cogs subfolders"""
    import pathlib
    base_dir = pathlib.Path(__file__).parent
    cog_directories = ['cogs.casino', 'cogs.utility']
    # --- HELPER MODULE FILTER ---
    utility_allow = {'help.py', 'ai.py', 'admin.py'}

    failures = []
    logger = logging.getLogger("casino.extensions")
    for directory in cog_directories:
        folder_path = base_dir / directory.replace('.', '/')
        if not folder_path.exists():
            folder_path.mkdir(parents=True, exist_ok=True)
            continue

        for filename in sorted(os.listdir(folder_path)):
            if filename.endswith('.py') and not filename.startswith('__'):
                if directory == 'cogs.casino':
                    # --- NON-COG MODULES ---
                    allowed = filename not in {'bank.py', 'permissions.py', 'game_registry.py'}
                elif directory == 'cogs.utility':
                    allowed = filename in utility_allow
                else:
                    allowed = False

                if not allowed:
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
    if not config.TOKEN:
        logging.getLogger("casino.startup").error(
            "DISCORD_TOKEN is missing. Configure the host environment using .env.example."
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