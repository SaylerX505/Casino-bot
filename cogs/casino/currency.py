import asyncio
import logging
import random

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

import config
from cogs.casino import bank, permissions
from cogs.casino.currency_catalog import (
    CONSUMABLE_REWARDS,
    DAILY_REWARD_MAX,
    DAILY_REWARD_MIN,
    fmt,
    get_coin_emoji,
)
from cogs.casino.currency_views import (
    CurrencyHubLayout,
    build_inventory_view,
    build_profile_panel,
)
from cogs.casino.game_assets import SERVER_LOGO_URL
from cogs.casino.permissions import configured_log_channel_id
from cogs.utility.theme import StatusPanel, embed_to_v2

LOG_CHANNEL_ID = int(getattr(config, "CURRENCY_LOG_CHANNEL_ID", 0) or 0)
logger = logging.getLogger(__name__)

class TransferConfirmationView(LayoutView):
    """Owner-bound, single-use confirmation for an atomic wallet transfer."""

    def __init__(self, cog, sender, recipient, amount: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.sender = sender
        self.recipient = recipient
        self.amount = int(amount)
        self.coin_emoji = get_coin_emoji(getattr(sender, "guild", None))
        self.done = False
        self.message = None
        self._panel_text = TextDisplay(
            "# تأكيد التحويل\n"
            f"تحويل **{fmt(self.amount)} {self.coin_emoji}** إلى **{recipient.display_name}**؟\n"
            "لن يتم خصم أي مبلغ قبل التأكيد."
        )
        self.confirm_button = discord.ui.Button(
            label="تأكيد التحويل",
            style=discord.ButtonStyle.secondary,
            custom_id="currency_transfer_confirm",
        )
        self.confirm_button.callback = self.confirm_transfer
        self.cancel_button = discord.ui.Button(
            label="إلغاء",
            style=discord.ButtonStyle.secondary,
            custom_id="currency_transfer_cancel",
        )
        self.cancel_button.callback = self.cancel_transfer
        self.add_item(
            Container(
                self._panel_text,
                Separator(),
                ActionRow(self.confirm_button, self.cancel_button),
                accent_color=config.EMBED_COLOR,
            )
        )

    def _close_controls(self):
        self.confirm_button.disabled = True
        self.cancel_button.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.sender.id:
            await interaction.response.send_message(
                "ده تأكيد التحويل الخاص بعضو تاني.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        return True

    async def confirm_transfer(self, interaction: discord.Interaction):
        if self.done:
            return await interaction.response.send_message(
                "تم إغلاق تأكيد التحويل.",
                ephemeral=True,
            )
        try:
            await interaction.response.defer()
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning(
                "Could not acknowledge transfer confirmation sender_id=%s recipient_id=%s",
                self.sender.id,
                self.recipient.id,
                exc_info=True,
            )
            return

        self.done = True
        self._close_controls()
        try:
            transferred, balance_after = await asyncio.to_thread(
                bank.transfer_balance,
                self.sender.id,
                self.recipient.id,
                self.amount,
            )
        except Exception:
            logger.exception(
                "Currency transfer failed sender_id=%s recipient_id=%s",
                self.sender.id,
                self.recipient.id,
            )
            self._panel_text.content = (
                "# تعذّر إتمام التحويل\n"
                "لم يتم تأكيد العملية. راجع رصيدك قبل إعادة المحاولة."
            )
        else:
            if transferred:
                self._panel_text.content = (
                    "# تم التحويل بنجاح\n"
                    f"المستلم: **{self.recipient.display_name}**\n"
                    f"المبلغ: **{fmt(self.amount)} {self.coin_emoji}**\n"
                    f"رصيدك الجديد: **{fmt(balance_after)} {self.coin_emoji}**"
                )
                try:
                    await self.cog.log_action(
                        interaction.guild,
                        "Currency Transfer",
                        (
                            f"From {self.sender.id} to {self.recipient.id}: "
                            f"{fmt(self.amount)}"
                        ),
                        discord.Color.orange(),
                    )
                except Exception:
                    logger.exception(
                        "Transfer committed but audit log failed sender_id=%s recipient_id=%s",
                        self.sender.id,
                        self.recipient.id,
                    )
            elif int(balance_after) < self.amount:
                self._panel_text.content = (
                    "# التحويل لم يتم\n"
                    f"رصيدك الحالي: **{fmt(balance_after)} {self.coin_emoji}**."
                )
            else:
                self._panel_text.content = (
                    "# التحويل لم يتم\n"
                    "لم يتم خصم أي عملات. حاول مرة أخرى لاحقًا."
                )

        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            logger.warning(
                "Could not refresh currency transfer confirmation sender_id=%s recipient_id=%s",
                self.sender.id,
                self.recipient.id,
                exc_info=True,
            )
        self.stop()

    async def cancel_transfer(self, interaction: discord.Interaction):
        if self.done:
            return await interaction.response.send_message(
                "تم إغلاق تأكيد التحويل.",
                ephemeral=True,
            )
        self.done = True
        self._close_controls()
        self._panel_text.content = "# تم إلغاء التحويل\nلم يتم خصم أي عملات."
        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.warning(
                "Could not update cancelled transfer sender_id=%s recipient_id=%s",
                self.sender.id,
                self.recipient.id,
                exc_info=True,
            )
        self.stop()

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        self._close_controls()
        self._panel_text.content = "# انتهى التأكيد\nلم يتم تنفيذ التحويل."
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Could not close expired transfer confirmation", exc_info=True)
        self.stop()


class Currency(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._persistent_hub_view = None

    async def cog_load(self):
        """Register exactly one persistent economy hub, including on extension reload."""
        old_view = getattr(self.bot, "_currency_hub_layout", None)
        if old_view is not None:
            old_view.stop()

        self._persistent_hub_view = CurrencyHubLayout(self)
        self.bot.add_view(self._persistent_hub_view)
        self.bot._currency_hub_layout = self._persistent_hub_view

    def cog_unload(self):
        """Unregister the same persistent view when reloading or unloading this extension."""
        view = self._persistent_hub_view
        if view is None:
            return
        view.stop()
        if getattr(self.bot, "_currency_hub_layout", None) is view:
            delattr(self.bot, "_currency_hub_layout")
        self._persistent_hub_view = None

    def check_whitelist(self, user: discord.Member) -> bool:
        guild = getattr(user, "guild", None)
        if guild is not None and permissions.has_manager_access(user, guild.id):
            return True
        if guild is not None and bank.is_guild_admin(guild.id, user.id):
            return True

        wl = bank.get_whitelist()
        if user.id in wl["users"]:
            return True
        role_ids = {role.id for role in getattr(user, "roles", ())}
        return bool(role_ids.intersection(wl["roles"]))
    async def _short_feedback(self, ctx, message: str):
        if ctx.interaction is None:
            return await ctx.send(
                message,
                delete_after=2,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return await ctx.send(
            view=StatusPanel("تعذّر تنفيذ العملية", message, "warning"),
            ephemeral=True,
        )

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
        if item_id not in CONSUMABLE_REWARDS:
            raise ValueError(f"Unsupported consumable: {item_id}")
        reward = random.choice(CONSUMABLE_REWARDS[item_id])
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
        embed.set_thumbnail(url=SERVER_LOGO_URL)
        try:
            await channel.send(view=embed_to_v2(embed), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            logger.warning("Could not send economy audit log (%s)", type(exc).__name__)

    @commands.hybrid_command(name='bal', aliases=['balance', 'cash', 'wallet', 'money', 'رصيد', 'فلوسي', 'محفظتي'], description='عرض رصيدك الحالي')
    async def balance(self, ctx):
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        try:
            balance = await asyncio.to_thread(self.get_balance, ctx.author.id)
        except Exception:
            logger.exception("Could not load balance requester_id=%s", ctx.author.id)
            return await self._short_feedback(ctx, "تعذّر عرض الرصيد.")
        await ctx.send(
            view=StatusPanel(
                "رصيدك",
                f"الرصيد الحالي: **{fmt(balance)} {get_coin_emoji(getattr(ctx, "guild", None))}**",
                "neutral",
            ),
            **send_kwargs,
        )

    @commands.hybrid_command(name="daily", aliases=["claim", "يومية"], description="استلام المكافأة اليومية")
    async def daily(self, ctx):
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        try:
            claim = await asyncio.to_thread(
                bank.claim_daily,
                ctx.author.id,
                bank.now(),
                random.randint(DAILY_REWARD_MIN, DAILY_REWARD_MAX),
            )
        except Exception:
            logger.exception("Daily reward transaction failed user_id=%s", ctx.author.id)
            return await self._short_feedback(ctx, "تعذّر استلام اليومية.")

        if not claim["claimed"]:
            if claim.get("reason") == "balance_limit":
                title = "المكافأة غير متاحة"
                description = "الرصيد وصل للحد الأقصى المدعوم."
            else:
                next_available_time = int(claim["last_daily"] + 86400)
                title = "اليومية مستلمة بالفعل"
                description = f"تقدر تستلمها <t:{next_available_time}:R>."
            return await ctx.send(
                view=StatusPanel(title, description, "warning"),
                **send_kwargs,
            )

        await ctx.send(
            view=StatusPanel(
                "استلمت اليومية",
                (
                    f"المكافأة: **{fmt(claim['amount'])} {get_coin_emoji(getattr(ctx, "guild", None))}**\n"
                    f"الستريك: **{claim['streak']}** يوم."
                ),
                "success",
            ),
            **send_kwargs,
        )

    @commands.hybrid_command(
        name="تحويل",
        aliases=["ckrc", "transfer", "pay", "send"],
        description="تحويل عملات لعضو بعد التأكيد",
    )
    async def transfer(self, ctx, target: discord.Member, amount: int):
        if ctx.guild is None:
            return await self._short_feedback(ctx, "التحويل متاح داخل السيرفر فقط.")
        if target.id == ctx.author.id:
            return await self._short_feedback(ctx, "لا يمكن التحويل إلى حسابك.")
        if target.bot:
            return await self._short_feedback(ctx, "التحويل لحسابات البوتات غير متاح.")
        if amount <= 0:
            return await self._short_feedback(ctx, "المبلغ غير صالح.")

        try:
            balance = await asyncio.to_thread(self.get_balance, ctx.author.id)
        except Exception:
            logger.exception("Could not validate transfer balance sender_id=%s", ctx.author.id)
            return await self._short_feedback(ctx, "تعذّر التحقق من الرصيد.")
        if balance < amount:
            return await self._short_feedback(ctx, "الرصيد غير كافٍ.")

        view = TransferConfirmationView(self, ctx.author, target, amount)
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        try:
            view.message = await ctx.send(view=view, **send_kwargs)
        except Exception:
            view.stop()
            logger.exception(
                "Could not send transfer confirmation sender_id=%s recipient_id=%s",
                ctx.author.id,
                target.id,
            )
            raise

    @commands.hybrid_command(name='inv', aliases=['inventory', 'bag', 'backpack', 'شنطتي', 'حقيبتي'], description='عرض أغراضك في الشنطة')
    async def inventory(self, ctx):
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        try:
            view = await build_inventory_view(self, ctx.author)
        except Exception:
            logger.exception("Could not load inventory requester_id=%s", ctx.author.id)
            return await ctx.send(
                view=StatusPanel("تعذر فتح الشنطة", "حصل عطل أثناء قراءة الشنطة. جرّب تاني بعد شوية.", "warning"),
                **send_kwargs,
            )
        view.message = await ctx.send(view=view, **send_kwargs)

    @commands.hybrid_command(name='بروفايل', aliases=['profile', 'me', 'انا', 'whois'], description='عرض بروفايلك أو بروفايل عضو')
    async def profile(self, ctx, target: discord.Member = None):
        """عرض بطاقة البروفايل المشتركة من الأمر ومركز الاقتصاد."""
        user = target or ctx.author
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        try:
            panel = await build_profile_panel(self, user)
        except Exception:
            logger.exception("Could not load profile requester_id=%s target_id=%s", ctx.author.id, user.id)
            return await ctx.send(
                view=StatusPanel("تعذر فتح البروفايل", "حصل عطل أثناء قراءة بيانات العضو. جرّب تاني بعد شوية.", "warning"),
                **send_kwargs,
            )
        await ctx.send(view=panel, **send_kwargs)

    @commands.hybrid_command(name='استخدم', aliases=['use', 'open', 'افتح'], description='استخدام صندوق أو تذكرة حظ')
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

        try:
            redeemed, reward, balance_after = await asyncio.to_thread(
                self.redeem_consumable,
                ctx.author.id,
                item_id,
            )
        except Exception:
            logger.exception("Consumable redemption failed user_id=%s item_id=%s", ctx.author.id, item_id)
            return await ctx.send(
                view=StatusPanel(
                    "تعذر استخدام الغرض",
                    "حصل عطل أثناء تسجيل العملية. راجع رصيدك والشنطة قبل إعادة المحاولة.",
                    "warning",
                ),
                ephemeral=private,
            )

        if not redeemed:
            try:
                item_count = await asyncio.to_thread(self.get_item_count, ctx.author.id, item_id)
            except Exception:
                logger.exception("Could not inspect item after failed redemption user_id=%s item_id=%s", ctx.author.id, item_id)
                return await ctx.send(
                    view=StatusPanel("تعذر التحقق من الغرض", "قاعدة البيانات لم تؤكد حالة الغرض. جرّب تاني بعد شوية.", "warning"),
                    ephemeral=private,
                )
            if item_count > 0:
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
            f"استخدمت **{item_name}** وكسبت **{fmt(reward)} {get_coin_emoji(getattr(ctx, "guild", None))}**.\n"
            f"رصيدك الجديد: **{fmt(balance_after)} {get_coin_emoji(getattr(ctx, "guild", None))}**."
        )
        await ctx.send(
            view=StatusPanel("🍀 حظك!", description, "success"),
            ephemeral=private,
        )


async def setup(bot):
    """Let Cog lifecycle own persistent-view registration and cleanup."""
    await bot.add_cog(Currency(bot))
