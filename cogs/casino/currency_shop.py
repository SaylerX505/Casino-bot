"""Store UI and purchase confirmation flows."""

import asyncio
import logging

import discord
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

import config
from cogs.casino import bank
from cogs.casino.currency_catalog import INVENTORY_ITEMS, fmt, get_coin_emoji
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


async def build_shop_view(cog, owner=None):
    balance = None
    if owner is not None:
        balance = await asyncio.to_thread(cog.get_balance, owner.id)
    return ShopView(cog, owner, balance=balance)


class BuyForm(discord.ui.Modal, title="شراء غرض"):
    def __init__(self, cog, item_id, price, item_name):
        super().__init__()
        self.cog = cog
        self.item_id = item_id
        self.price = price
        self.item_name = item_name
        self.add_item(discord.ui.TextInput(
            label="الكمية", 
            placeholder="اكتب عدد القطع...", 
            required=True
        ))
        
    async def on_submit(self, interaction: discord.Interaction):
        coin_emoji = get_coin_emoji(getattr(interaction, 'guild', None))
        try:
            quantity = int(self.children[0].value.strip())
        except ValueError:
            return await interaction.response.send_message(
                view=StatusPanel("كمية غير صالحة", "اكتب رقم صحيح للكمية.", "warning"),
                ephemeral=True,
            )

        if quantity <= 0:
            return await interaction.response.send_message(
                view=StatusPanel("كمية غير صالحة", "الكمية لازم تكون 1 على الأقل.", "warning"),
                ephemeral=True,
            )

        total_cost = quantity * self.price
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning(
                "Could not acknowledge shop purchase interaction user_id=%s item_id=%s",
                interaction.user.id,
                self.item_id,
                exc_info=True,
            )
            return

        try:
            purchased, balance_after = await asyncio.to_thread(
                bank.purchase_item,
                interaction.user.id,
                self.item_id,
                quantity,
                self.price,
            )
        except Exception:
            logger.exception(
                "Shop purchase transaction failed user_id=%s item_id=%s",
                interaction.user.id,
                self.item_id,
            )
            return await interaction.followup.send(
                view=StatusPanel(
                    "تعذر تأكيد الشراء",
                    "حصل عطل أثناء معالجة العملية. راجع رصيدك والشنطة قبل إعادة المحاولة؛ ما هنعلنش شراء ناجح من غير تأكيد قاعدة البيانات.",
                    "warning",
                ),
                ephemeral=True,
            )

        if not purchased:
            return await interaction.followup.send(
                view=StatusPanel(
                    "تعذر الشراء",
                    f"رصيدك الحالي **{fmt(balance_after)} {coin_emoji}**، ومحتاج **{fmt(total_cost)} {coin_emoji}** لشراء {quantity} × {self.item_name}.",
                    "warning",
                ),
                ephemeral=True,
            )

        await interaction.followup.send(
            view=StatusPanel(
                "تم الشراء",
                f"اشتريت **{quantity} × {self.item_name}** بمبلغ **{fmt(total_cost)} {coin_emoji}**.\nرصيدك الجديد: **{fmt(balance_after)} {coin_emoji}**.",
                "success",
            ),
            ephemeral=True,
        )
class ShopDropdown(discord.ui.Select):
    def __init__(self, cog, guild=None):
        self.cog = cog
        self.prices = {item[0]: item[3] for item in INVENTORY_ITEMS}
        self.names = {item[0]: f"{item[5]} {item[1]}" for item in INVENTORY_ITEMS}
        coin_emoji = get_coin_emoji(guild)
        options = [
            discord.SelectOption(
                label=name,
                description=f"{fmt(price)} {coin_emoji} | {description}",
                value=item_id,
                emoji=emoji,
            )
            for item_id, name, category, price, description, emoji in INVENTORY_ITEMS
        ]
        super().__init__(
            placeholder="اختار غرض من دكان الحفرة... 🛒",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_id = self.values[0]
        await interaction.response.send_modal(
            BuyForm(self.cog, item_id, self.prices[item_id], self.names[item_id])
        )


class ShopView(LayoutView):
    def __init__(self, cog, owner=None, *, balance=None):
        super().__init__(timeout=180)
        self.message = None
        self.coin_emoji = get_coin_emoji(getattr(owner, "guild", None))
        if owner is not None and balance is None:
            raise ValueError("ShopView requires an asynchronously loaded balance snapshot")
        balance_line = (
            f"**رصيدك الحالي:** {fmt(balance)} {self.coin_emoji}\n"
            if owner is not None
            else ""
        )
        container = Container(
            TextDisplay(
                "# 🛒 دكان الحفرة\n"
                f"{balance_line}"
                "اختار الغرض اللي محتاجه، راجع السعر، وحدد الكمية قبل الشراء.\n"
                "السعر ظاهر لكل غرض، والشراء بيتأكد من الرصيد قبل إضافة الأغراض."
            ),
            Separator(),
            accent_color=config.EMBED_COLOR,
        )
        row = ActionRow()
        row.add_item(ShopDropdown(cog, getattr(owner, "guild", None)))
        container.add_item(row)
        self.add_item(container)

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Could not disable expired shop controls", exc_info=True)
