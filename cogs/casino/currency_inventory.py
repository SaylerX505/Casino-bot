"""Inventory screens, profile snapshots, and consumable redemption."""

import asyncio
import logging

import discord
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

import config
from cogs.casino import bank
from cogs.casino.currency_catalog import INVENTORY_ITEMS, fmt, get_coin_emoji
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


def _load_inventory_snapshot(cog, user_id):
    """Read a consistent-enough inventory screen snapshot off the event loop."""
    inventory = dict(bank.get_inventory(user_id))
    balance = int(bank.get_balance(user_id))
    return inventory, balance


def _load_profile_snapshot(cog, user_id):
    inventory, balance = _load_inventory_snapshot(cog, user_id)
    _last_daily, streak = bank.get_daily(str(user_id))
    return balance, int(streak), inventory


async def build_inventory_view(cog, owner, category="all", notice=None):
    inventory, balance = await asyncio.to_thread(_load_inventory_snapshot, cog, owner.id)
    return InventoryView(
        cog,
        owner,
        category=category,
        notice=notice,
        inventory=inventory,
        balance=balance,
    )


def render_profile_panel(user, balance, streak, inventory):
    """Render the V2 profile card from an already-loaded account snapshot."""
    labels = {item[0]: f"{item[5]} {item[1]}" for item in INVENTORY_ITEMS}
    items = " • ".join(
        f"{labels.get(item_id, item_id)} ×{fmt(quantity)}"
        for item_id, quantity in inventory.items()
        if quantity > 0
    ) or "الشنطة فاضية حاليًا"
    coin_emoji = get_coin_emoji(getattr(user, "guild", None))
    description = (
        f"**الرصيد:** {fmt(balance)} {coin_emoji}\n"
        f"**الستريك اليومي:** {streak} يوم\n\n"
        f"**الأغراض:**\n{items[:2200]}"
    )
    return StatusPanel(f"🪪 بروفايل {user.display_name}", description, "success")


async def build_profile_panel(cog, user):
    balance, streak, inventory = await asyncio.to_thread(_load_profile_snapshot, cog, user.id)
    return render_profile_panel(user, balance, streak, inventory)


class InventoryView(LayoutView):
    def __init__(
        self,
        cog,
        owner,
        category="all",
        notice=None,
        *,
        inventory=None,
        balance=None,
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.owner = owner
        self.owner_id = owner.id
        self.coin_emoji = get_coin_emoji(getattr(owner, "guild", None))
        self.category = category
        self.notice = notice
        self.message = None
        if inventory is None or balance is None:
            raise ValueError("InventoryView requires an asynchronously loaded inventory/balance snapshot")
        self.inventory = dict(inventory)
        self.balance = int(balance)

        self._panel_text = TextDisplay(self.make_description())
        container = Container(
            self._panel_text,
            Separator(),
            accent_color=config.EMBED_COLOR,
        )
        category_row = ActionRow()
        self.category_select = discord.ui.Select(
            placeholder="اختار قسم الشنطة...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="كل الأغراض", value="all", emoji="🎒"),
                discord.SelectOption(label="أدوات الألعاب", value="tools", emoji="🎮"),
                discord.SelectOption(label="المستهلكات", value="consumables", emoji="🎁"),
            ],
        )
        self.category_select.callback = self.select_category
        category_row.add_item(self.category_select)
        container.add_item(category_row)

        use_row = ActionRow()
        self.box_button = discord.ui.Button(
            label="استخدم صندوق",
            style=discord.ButtonStyle.secondary,
            custom_id="inventory_use_box",
            disabled=self.inventory.get("loot_box", 0) < 1,
        )
        self.box_button.callback = self.use_box_button
        use_row.add_item(self.box_button)
        self.ticket_button = discord.ui.Button(
            label="استخدم تذكرة",
            style=discord.ButtonStyle.secondary,
            custom_id="inventory_use_ticket",
            disabled=self.inventory.get("luck_ticket", 0) < 1,
        )
        self.ticket_button.callback = self.use_ticket_button
        use_row.add_item(self.ticket_button)
        container.add_item(use_row)
        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                view=StatusPanel(
                    "الشنطة دي شخصية",
                    "كل عضو له شنطته الخاصة. افتح مركز الاقتصاد واختار «الشنطة» عشان تشوف أغراضك.",
                    "warning",
                ),
                ephemeral=True,
            )
            return False
        return True

    def refresh_buttons(self):
        self.box_button.disabled = self.inventory.get("loot_box", 0) < 1
        self.ticket_button.disabled = self.inventory.get("luck_ticket", 0) < 1

    async def refresh_snapshot(self):
        """Refresh wallet and inventory off the event loop after a state mutation."""
        self.inventory, self.balance = await asyncio.to_thread(
            _load_inventory_snapshot,
            self.cog,
            self.owner_id,
        )
        self.refresh_buttons()
        self._panel_text.content = self.make_description()

    def make_description(self, notice=None):
        inventory = self.inventory
        lines = []
        for item_id, name, category, price, description, emoji in INVENTORY_ITEMS:
            if self.category != "all" and category != self.category:
                continue
            quantity = inventory.get(item_id, 0)
            if quantity < 1:
                continue
            lines.append(
                f"{emoji} **{name} ×{quantity}** — {description}\n"
                f"السعر في المتجر: **{fmt(price)}** {self.coin_emoji}"
            )

        if lines:
            description = "\n\n".join(lines)
        elif self.category == "tools":
            description = "مفيش أدوات ألعاب في القسم ده حاليًا. تقدر تشتري من مركز الاقتصاد."
        elif self.category == "consumables":
            description = "مفيش مستهلكات في القسم ده حاليًا. تقدر تشتري صندوق أو تذكرة من المتجر."
        else:
            description = "شنطتك فاضية حاليًا. افتح مركز الاقتصاد واختار المتجر عشان تشتري أول غرض."

        current_notice = self.notice if notice is None else notice
        if current_notice:
            description = f"{current_notice}\n\n{description}"
        balance_line = f"\n\n**الرصيد الحالي:** {fmt(self.balance)} {self.coin_emoji}"
        return f"# 🎒 شنطة {self.owner.display_name}\n{description}{balance_line}"

    def make_embed(self, notice=None):
        return discord.Embed(
            title=f"🎒 شنطة {self.owner.display_name}",
            description=self.make_description(notice),
            color=discord.Color.dark_purple(),
        )

    async def select_category(self, interaction: discord.Interaction):
        # Mutate the same view. Replacing it leaves the old view's timeout alive,
        # which could later overwrite the newer panel with stale controls/content.
        self.category = self.category_select.values[0]
        self._panel_text.content = self.make_description()
        self.refresh_buttons()
        await interaction.response.edit_message(view=self)

    async def redeem_consumable(self, interaction: discord.Interaction, item_id: str):
        # Acknowledge before touching SQLite so lock contention cannot expire the
        # component interaction. Atomic redemption remains the bank's source of truth.
        try:
            await interaction.response.defer()
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning(
                "Could not acknowledge inventory item redemption user_id=%s item_id=%s",
                self.owner_id,
                item_id,
                exc_info=True,
            )
            return

        try:
            redeemed, reward, balance_after = await asyncio.to_thread(
                self.cog.redeem_consumable,
                self.owner_id,
                item_id,
            )
        except Exception:
            logger.exception(
                "Inventory item redemption failed user_id=%s item_id=%s",
                self.owner_id,
                item_id,
            )
            return await interaction.followup.send(
                view=StatusPanel(
                    "تعذر استخدام الغرض",
                    "حصل عطل أثناء معالجة الغرض. راجع رصيدك والشنطة قبل المحاولة مرة تانية.",
                    "warning",
                ),
                ephemeral=True,
            )

        if not redeemed:
            try:
                await self.refresh_snapshot()
            except Exception:
                logger.exception("Could not refresh inventory after rejected redemption user_id=%s", self.owner_id)
                return await interaction.followup.send(
                    view=StatusPanel(
                        "تعذر تحديث الشنطة",
                        "لم نستهلك الغرض، لكن تعذر قراءة الشنطة حاليًا. افتح الشنطة من جديد للتحقق.",
                        "warning",
                    ),
                    ephemeral=True,
                )
            if self.inventory.get(item_id, 0) > 0:
                message = "رصيدك وصل للحد الأقصى؛ احتفظنا بالغرض في شنطتك ولم نستهلكه."
            else:
                message = "الغرض ده مش موجود في شنطتك. افتح المتجر من مركز الاقتصاد لشرائه."
            self._panel_text.content = self.make_description(message)
            try:
                await interaction.edit_original_response(view=self)
            except discord.HTTPException:
                logger.warning("Could not refresh inventory after rejected redemption user_id=%s", self.owner_id, exc_info=True)
            return await interaction.followup.send(
                view=StatusPanel("الغرض غير مستخدم", message, "warning"),
                ephemeral=True,
            )

        if item_id == "loot_box":
            notice = f"🎁 فتحت صندوق الحظ وكسبت **{fmt(reward)}** {self.coin_emoji}."
        else:
            notice = f"🎟️ استخدمت تذكرة الحظ وكسبت **{fmt(reward)}** {self.coin_emoji}."
        notice += f"\nرصيدك الجديد: **{fmt(balance_after)}** {self.coin_emoji}"
        self.notice = notice
        try:
            await self.refresh_snapshot()
        except Exception:
            logger.exception("Item redeemed but inventory snapshot refresh failed user_id=%s", self.owner_id)
            self.inventory[item_id] = max(0, self.inventory.get(item_id, 0) - 1)
            self.balance = int(balance_after)
            self.refresh_buttons()
            self._panel_text.content = self.make_description()
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            logger.warning(
                "Item redeemed but inventory panel could not be refreshed user_id=%s item_id=%s",
                self.owner_id,
                item_id,
                exc_info=True,
            )
            await interaction.followup.send(
                view=StatusPanel("تم استخدام الغرض", notice, "success"),
                ephemeral=True,
            )

    async def use_box_button(self, interaction: discord.Interaction):
        await self.redeem_consumable(interaction, "loot_box")

    async def use_ticket_button(self, interaction: discord.Interaction):
        await self.redeem_consumable(interaction, "luck_ticket")

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Could not disable expired inventory controls", exc_info=True)
