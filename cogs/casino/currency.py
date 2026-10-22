import logging
import random

import discord
from discord.ext import commands

import config
from cogs.casino import bank
from cogs.casino.currency_catalog import COIN_EMOJI, INVENTORY_ITEMS, fmt
from cogs.casino.currency_views import CurrencyHubLayout, InventoryView
from cogs.casino.permissions import configured_log_channel_id
from cogs.utility.theme import StatusPanel, embed_to_v2

LOG_CHANNEL_ID = int(getattr(config, "CURRENCY_LOG_CHANNEL_ID", 0) or 0)
logger = logging.getLogger(__name__)
SERVER_LOGO = "https://media.discordapp.net/attachments/1509650275164749994/1513741255786627162/c51bafdd9c1feb13e22458c35067c718.webp"

class Currency(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._persistent_hub_view = None

    async def cog_load(self):
        """Restore persistent public economy buttons when the extension loads."""
        self._persistent_hub_view = CurrencyHubLayout(self)
        self.bot.add_view(self._persistent_hub_view)

    def cog_unload(self):
        """Unregister the view when reloading or unloading this extension."""
        if self._persistent_hub_view is not None:
            self.bot.remove_view(self._persistent_hub_view)
            self._persistent_hub_view = None

    def check_whitelist(self, user: discord.Member) -> bool:
        permissions = getattr(user, "guild_permissions", None)
        if getattr(permissions, "administrator", False):
            return True

        guild = getattr(user, "guild", None)
        if guild is not None and bank.is_guild_admin(guild.id, user.id):
            return True

        wl = bank.get_whitelist()
        if user.id in wl["users"]:
            return True
        role_ids = {role.id for role in getattr(user, "roles", ())}
        return bool(role_ids.intersection(wl["roles"]))
    def update_balance(self, user_id: int, amount: int):
        return bank.update_balance(user_id, amount)

    def credit_balance(self, user_id: int, amount: int):
        """Safely credit coins without raising on a SQLite balance limit."""
        return bank.credit_balance(user_id, amount)

    def debit_balance(self, user_id: int, amount: int):
        return bank.debit_balance(user_id, amount)

    def charge_up_to(self, user_id: int, amount: int):
        return bank.charge_up_to(user_id, amount)

    def settle_wheel(self, user_id: int, payout: int = 0, target_loss: int | None = None):
        return bank.settle_wheel(user_id, 50, payout=payout, target_loss=target_loss)
    def place_bet(self, player_a: int, player_b: int, amount: int) -> bool:
        return bank.place_bet(player_a, player_b, amount)

    def get_balance(self, user_id: int) -> int: return bank.get_balance(user_id)

    def add_item(self, user_id: int, item: str, amount: int = 1):
        return bank.add_item(user_id, item, amount)

    def remove_item(self, user_id: int, item: str, amount: int = 1) -> bool:
        return bank.remove_item(user_id, item, amount)

    def get_item_count(self, user_id: int, item: str) -> int:
        return bank.get_item_count(user_id, item)

    def get_inventory(self, user_id: int) -> dict:
        return bank.get_inventory(user_id)

    def redeem_consumable(self, user_id: int, item_id: str):
        rewards = {
            "loot_box": [30, 50, 80, 100, 150, 250, 400],
            "luck_ticket": [20, 40, 60, 100, 150],
        }
        if item_id not in rewards:
            raise ValueError(f"Unsupported consumable: {item_id}")
        reward = random.choice(rewards[item_id])
        redeemed, balance_after = bank.redeem_item(user_id, item_id, reward)
        return redeemed, reward, balance_after

    async def log_action(self, source_guild: discord.Guild, title: str, description: str, color: discord.Color):
        """Write economy audit events only to a log channel in the originating guild."""
        if source_guild is None:
            logger.warning("Skipped economy audit log without a source guild")
            return

        channel_id = configured_log_channel_id(
            source_guild.id,
            "economy",
            LOG_CHANNEL_ID,
        )
        if not channel_id:
            logger.debug("Economy audit logging is disabled for guild=%s", source_guild.id)
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.warning("Could not resolve economy log channel (%s)", type(exc).__name__)
                return

        target_guild_id = getattr(getattr(channel, "guild", None), "id", None)
        if target_guild_id != source_guild.id:
            logger.error(
                "Skipped economy audit log because configured channel guild %s does not match source guild %s",
                target_guild_id,
                source_guild.id,
            )
            return

        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text=f"Bank Log • {config.BRAND}")
        embed.set_thumbnail(url=SERVER_LOGO)
        try:
            await channel.send(view=embed_to_v2(embed), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            logger.warning("Could not send economy audit log (%s)", type(exc).__name__)

    @commands.hybrid_command(name='bal', aliases=['balance', 'رصيد', 'فلوسي', 'محفظتي'], description='عرض رصيدك وفتح مركز الاقتصاد')
    async def balance(self, ctx):
        view = CurrencyHubLayout(self, ctx.author)
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        await ctx.send(view=view, **send_kwargs)

    @commands.hybrid_command(name='inv', aliases=['inventory', 'شنطتي', 'حقيبتي'], description='عرض أغراضك في الشنطة')
    async def inventory(self, ctx):
        view = InventoryView(self, ctx.author)
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        view.message = await ctx.send(view=view, **send_kwargs)

    @commands.hybrid_command(name='بروفايل', aliases=['profile', 'me', 'انا'], description='عرض بروفايلك أو بروفايل عضو')
    async def profile(self, ctx, target: discord.Member = None):
        """كارت البروفايل بتاعك: رصيد + ستريك + اغراض"""
        user = target or ctx.author
        bal = self.get_balance(user.id)
        _last, streak = bank.get_daily(str(user.id))
        inventory = self.get_inventory(user.id)
        labels = {item[0]: f"{item[5]} {item[1]}" for item in INVENTORY_ITEMS}
        items = " • ".join(
            f"{labels.get(item_id, item_id)} ×{fmt(quantity)}"
            for item_id, quantity in inventory.items()
            if quantity > 0
        ) or "الشنطة فاضية حاليًا"
        description = (
            f"**الرصيد:** {fmt(bal)} {COIN_EMOJI}\n"
            f"**الستريك اليومي:** {streak} يوم\n\n"
            f"**الأغراض:**\n{items[:2200]}"
        )
        await ctx.send(
            view=StatusPanel(f"🪪 بروفايل {user.display_name}", description, "success"),
            ephemeral=ctx.interaction is not None,
        )

    @commands.hybrid_command(name='استخدم', aliases=['use', 'افتح'], description='استخدام صندوق أو تذكرة حظ')
    async def use_item(self, ctx, item: str | None = None):
        private = ctx.interaction is not None
        if not item:
            return await ctx.send(
                view=StatusPanel("حدد الغرض", "اكتب `صندوق` أو `تذكرة` عشان تستخدم الغرض الموجود في شنطتك.", "warning"),
                ephemeral=private,
            )

        normalized = item.strip().lower()
        if normalized in ("صندوق", "box", "loot", "loot_box"):
            item_id = "loot_box"
        elif normalized in ("تذكرة", "ticket", "luck", "luck_ticket"):
            item_id = "luck_ticket"
        else:
            return await ctx.send(
                view=StatusPanel("غرض غير معروف", "المتاح حاليًا هو صندوق الحظ أو تذكرة الحظ. أدوات الألعاب بتتفعّل داخل جولاتها.", "warning"),
                ephemeral=private,
            )

        redeemed, reward, balance_after = self.redeem_consumable(ctx.author.id, item_id)
        if not redeemed:
            if self.get_item_count(ctx.author.id, item_id) > 0:
                title = "الرصيد وصل للحد الأقصى"
                description = "احتفظنا بالغرض في شنطتك، ومش هنستهلكه لحد ما يبقى فيه مساحة في الرصيد."
            else:
                title = "الغرض مش موجود في شنطتك"
                description = "اشترِ صندوق الحظ أو تذكرة الحظ من المتجر الأول."
            return await ctx.send(
                view=StatusPanel(title, description, "warning"),
                ephemeral=private,
            )

        item_name = "صندوق الحظ" if item_id == "loot_box" else "تذكرة الحظ"
        description = (
            f"استخدمت **{item_name}** وكسبت **{fmt(reward)} {COIN_EMOJI}**.\n"
            f"رصيدك الجديد: **{fmt(balance_after)} {COIN_EMOJI}**."
        )
        await ctx.send(
            view=StatusPanel("🍀 حظك!", description, "success"),
            ephemeral=private,
        )

async def setup(bot):
    cog = Currency(bot)
    await bot.add_cog(cog)
    old_layout = getattr(bot, "_currency_hub_layout", None)
    if old_layout is not None:
        bot.remove_view(old_layout)
    layout = CurrencyHubLayout(cog)
    bot.add_view(layout)
    bot._currency_hub_layout = layout
