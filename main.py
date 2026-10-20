# --- BOT ENTRY POINT ---
import discord
from discord.ext import commands
import os
import asyncio
import config

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
ERROR_LOG_CHANNEL_ID = 1486822134109180049

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
            await ctx.send("❌ الأمر ده مش متاح لصلاحيات حسابك.", ephemeral=ctx.interaction is not None)
        except discord.HTTPException:
            pass
        return
    if isinstance(error, commands.CommandOnCooldown):
        try:
            await ctx.send(
                f"⏳ اهدى يا معلم! جرب تاني بعد **{error.retry_after:.0f}** ثانية.",
                ephemeral=ctx.interaction is not None,
            )
        except discord.HTTPException:
            pass
        return

    err_text = f"{type(error).__name__}: {error}"[:1500]
    command_text = ctx.command.qualified_name if ctx.command else "unknown"
    message_text = ctx.message.content if ctx.message else f"/{command_text}"
    print(f"❌ Command error in {command_text}: {err_text}")
    try:
        channel = bot.get_channel(ERROR_LOG_CHANNEL_ID)
        if channel is not None:
            embed = discord.Embed(
                title="🚨 Bot Error",
                description=(
                    f"<@{config.NEGLAWY_ID}> فيه ايرور يا مطور!\n\n"
                    f"**الأمر:** `{command_text}`\n"
                    f"**اليوزر:** {ctx.author.mention} (`{ctx.author.id}`)\n"
                    f"**الروم:** {ctx.channel.mention}\n"
                    f"**الرسالة:** {message_text[:300]}\n\n"
                    f"```{err_text}```"
                ),
                color=0x8B1E1E,
            )
            await channel.send(content=f"<@{config.NEGLAWY_ID}>", embed=embed)
    except Exception as log_error:
        print(f"⚠️ Failed to log error: {log_error}")

    try:
        await ctx.send(
            "❌ حصلت مشكلة! نجلاوي اتبلغ وهيظبطها. جرب تاني كمان شوية.",
            ephemeral=ctx.interaction is not None,
        )
    except discord.HTTPException:
        pass
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