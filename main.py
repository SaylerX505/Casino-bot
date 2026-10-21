# --- BOT ENTRY POINT ---
import discord
from discord.ext import commands
import os
import asyncio
import logging
import config
from discord import app_commands

# --- BOT CONFIGURATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=config.PREFIX, 
    intents=intents, 
    case_insensitive=True,
    owner_ids=config.OWNER_IDS
)
bot.remove_command('help')

# --- EVENT HANDLERS ---
ERROR_LOG_CHANNEL_ID = config.LOG_CHANNELS.get("command_errors", 0)


async def _send_command_error(ctx, message):
    """Use ephemeral responses only for interaction contexts, never prefix messages."""
    kwargs = {"ephemeral": True} if getattr(ctx, "interaction", None) is not None else {}
    await ctx.send(message, **kwargs)


@bot.event
async def on_ready():
    print(f'🔥 Bot is online! Logged in as {bot.user}')
    try:
        await bot.change_presence(status=discord.Status.dnd, activity=discord.CustomActivity(name='مرحبا المانيا, انا الحفرة'))
    except Exception as e:
        print(f"⚠️ Presence failed: {e}")
    
    if not getattr(bot, "_slash_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            bot._slash_commands_synced = True
            print(f"✅ Synced {len(synced)} slash command(s)")
        except Exception as e:
            print(f"❌ Failed to sync slash commands: {e}")

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
                    print(f"📦 Loaded Cog: {cog_path}")
                except Exception as e:
                    failures.append(f"{cog_path}: {type(e).__name__}: {e}")
                    print(f"❌ Failed to load {cog_path}: {e}")

    if failures:
        raise RuntimeError("Required extensions failed to load:\n" + "\n".join(failures))

# --- APPLICATION STARTUP ---
async def main():
    if not config.TOKEN:
        print("❌ DISCORD_TOKEN is missing! Put it in the .env file (see .env.example).")
        return
    async with bot:
        await load_cogs()
        await bot.start(config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())