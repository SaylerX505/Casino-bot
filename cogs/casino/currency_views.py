"""Economy hub V2 layout with compatibility exports for its subviews."""

import asyncio
import logging
import random

import discord
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

import config
from cogs.casino import bank
from cogs.casino.currency_catalog import (
    DAILY_REWARD_MAX,
    DAILY_REWARD_MIN,
    fmt,
    get_coin_emoji,
)
from cogs.casino.currency_inventory import (
    InventoryView,
    _load_inventory_snapshot,
    _load_profile_snapshot,
    build_inventory_view,
    build_profile_panel,
    render_profile_panel,
)
from cogs.casino.currency_shop import BuyForm, ShopDropdown, ShopView, build_shop_view
from cogs.casino.currency_transfer import TransferModal
from cogs.utility.theme import private_status

logger = logging.getLogger(__name__)


async def build_currency_hub_view(cog, requester=None):
    balance = None
    if requester is not None:
        balance = await asyncio.to_thread(cog.get_balance, requester.id)
    return CurrencyHubLayout(cog, requester, balance=balance)



class CurrencyHubLayout(LayoutView):
    def __init__(self, cog, requester=None, *, balance=None):
        super().__init__(timeout=None)
        self.cog = cog
        self.coin_emoji = get_coin_emoji(getattr(requester, "guild", None))

        balance_line = "افتح خزنتك وتابع رصيدك وأدِر أغراضك من مكان واحد."
        if requester is not None:
            if balance is None:
                raise ValueError("CurrencyHubLayout requires an asynchronously loaded balance snapshot")
            balance_line = f"**الرصيد الحالي:** {fmt(balance)} {self.coin_emoji}"

        container = Container(
            TextDisplay(
                f"# 🏦 خزنة الحفرة\n{balance_line}\n\n"
                "اختار العملية اللي محتاجها. نتائج العمليات الحساسة بتظهر لك بشكل خاص."
            ),
            Separator(),
            accent_color=config.EMBED_COLOR,
        )

        first_row = ActionRow()
        self._add_button(first_row, "اليومية", "hubv2_daily", self.daily_btn)
        self._add_button(first_row, "الأغنى", "hubv2_top", self.top_btn)
        self._add_button(first_row, "المتجر", "hubv2_shop", self.shop_btn)
        container.add_item(first_row)

        second_row = ActionRow()
        self._add_button(second_row, "تحويل", "hubv2_transfer", self.transfer_btn)
        self._add_button(second_row, "الشنطة", "hubv2_inventory", self.inventory_btn)
        self._add_button(second_row, "بروفايل", "hubv2_profile", self.profile_btn)
        container.add_item(second_row)
        self.add_item(container)

    @staticmethod
    def _add_button(row, label, custom_id, callback):
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
        )
        button.callback = callback
        row.add_item(button)

    async def daily_btn(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning("Could not acknowledge daily claim interaction user_id=%s", interaction.user.id, exc_info=True)
            return
        try:
            claim = await asyncio.to_thread(
                bank.claim_daily,
                interaction.user.id,
                bank.now(),
                random.randint(DAILY_REWARD_MIN, DAILY_REWARD_MAX),
            )
        except Exception:
            logger.exception("Daily reward transaction failed user_id=%s", interaction.user.id)
            return await private_status(
                interaction,
                "تعذر استلام اليومية",
                "حصل عطل أثناء تسجيل المكافأة. حاول تاني بعد شوية، وهنحافظ على مطالبتك لحد ما قاعدة البيانات تأكد النتيجة.",
                "warning",
            )

        if not claim["claimed"]:
            if claim.get("reason") == "balance_limit":
                return await private_status(
                    interaction,
                    "المكافأة غير متاحة",
                    "رصيدك وصل للحد الأقصى المدعوم؛ المكافأة لم تُصرف ولم تُستهلك مطالبتك اليومية.",
                    "warning",
                )
            next_available_time = int(claim["last_daily"] + 86400)
            return await private_status(
                interaction,
                "اليومية مستلمة بالفعل",
                f"تقدر ترجع تطالب بيها <t:{next_available_time}:R>.",
                "warning",
            )

        amount = claim["amount"]
        streak = claim["streak"]
        coin_emoji = get_coin_emoji(getattr(interaction, 'guild', None))
        await private_status(
            interaction,
            "استلمت اليومية",
            f"المكافأة: **{fmt(amount)} {coin_emoji}**\nالستريك الحالي: **{streak}** يوم.",
            "success",
        )

    async def top_btn(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning("Could not acknowledge leaderboard interaction user_id=%s", interaction.user.id, exc_info=True)
            return
        try:
            ranking = await asyncio.to_thread(bank.top_balances, 10)
        except Exception:
            logger.exception("Economy leaderboard query failed user_id=%s", interaction.user.id)
            return await private_status(
                interaction,
                "تعذر فتح الترتيب",
                "حصل عطل أثناء قراءة الرصيد. جرّب تاني بعد شوية.",
                "warning",
            )
        coin_emoji = get_coin_emoji(getattr(interaction, 'guild', None))
        description = "\n".join(
            f"**#{index + 1}** <@{user_id}> — **{fmt(balance)}** {coin_emoji}"
            for index, (user_id, balance) in enumerate(ranking)
        )
        await private_status(
            interaction,
            "🏆 أغنى الأعضاء",
            description or "الخزنة فاضية لسه!",
            "neutral",
        )
    async def shop_btn(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning("Could not acknowledge shop interaction user_id=%s", interaction.user.id, exc_info=True)
            return
        try:
            view = await build_shop_view(self.cog, interaction.user)
            await interaction.edit_original_response(view=view)
            view.message = await interaction.original_response()
        except Exception:
            logger.exception("Could not open economy shop user_id=%s", interaction.user.id)
            await private_status(interaction, "تعذر فتح المتجر", "حصل عطل أثناء تجهيز المتجر. جرّب تاني بعد شوية.", "warning")

    async def transfer_btn(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TransferModal(self.cog))

    async def inventory_btn(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning("Could not acknowledge inventory interaction user_id=%s", interaction.user.id, exc_info=True)
            return
        try:
            view = await build_inventory_view(self.cog, interaction.user)
            await interaction.edit_original_response(view=view)
            view.message = await interaction.original_response()
        except Exception:
            logger.exception("Could not open economy inventory user_id=%s", interaction.user.id)
            await private_status(interaction, "تعذر فتح الشنطة", "حصل عطل أثناء قراءة الشنطة. جرّب تاني بعد شوية.", "warning")

    async def profile_btn(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning("Could not acknowledge profile interaction user_id=%s", interaction.user.id, exc_info=True)
            return
        try:
            panel = await build_profile_panel(self.cog, interaction.user)
            await interaction.edit_original_response(view=panel)
        except Exception:
            logger.exception("Could not open economy profile user_id=%s", interaction.user.id)
            await private_status(interaction, "تعذر فتح البروفايل", "حصل عطل أثناء قراءة بياناتك. جرّب تاني بعد شوية.", "warning")

__all__ = [
    "BuyForm",
    "CurrencyHubLayout",
    "InventoryView",
    "ShopDropdown",
    "ShopView",
    "TransferModal",
    "_load_inventory_snapshot",
    "_load_profile_snapshot",
    "build_currency_hub_view",
    "build_inventory_view",
    "build_profile_panel",
    "build_shop_view",
    "render_profile_panel",
]
