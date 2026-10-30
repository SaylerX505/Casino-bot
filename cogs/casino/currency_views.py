"""Discord Components V2 presentation and interaction flows for the economy."""

import asyncio
import logging
import random
import re

import discord
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

import config
from cogs.casino import bank
from cogs.casino.currency_catalog import (
    COIN_EMOJI,
    DAILY_REWARD_MAX,
    DAILY_REWARD_MIN,
    INVENTORY_ITEMS,
    fmt,
)
from cogs.utility.theme import StatusPanel, private_status

logger = logging.getLogger(__name__)


def _load_inventory_snapshot(cog, user_id):
    """Read a consistent-enough inventory screen snapshot off the event loop."""
    inventory = dict(cog.get_inventory(user_id))
    balance = int(cog.get_balance(user_id))
    return inventory, balance


def _load_profile_snapshot(cog, user_id):
    inventory, balance = _load_inventory_snapshot(cog, user_id)
    _last_daily, streak = bank.get_daily(str(user_id))
    return balance, int(streak), inventory


async def build_shop_view(cog, owner=None):
    balance = None
    if owner is not None:
        balance = await asyncio.to_thread(cog.get_balance, owner.id)
    return ShopView(cog, owner, balance=balance)


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
    description = (
        f"**الرصيد:** {fmt(balance)} {COIN_EMOJI}\n"
        f"**الستريك اليومي:** {streak} يوم\n\n"
        f"**الأغراض:**\n{items[:2200]}"
    )
    return StatusPanel(f"🪪 بروفايل {user.display_name}", description, "success")


async def build_profile_panel(cog, user):
    balance, streak, inventory = await asyncio.to_thread(_load_profile_snapshot, cog, user.id)
    return render_profile_panel(user, balance, streak, inventory)


async def build_currency_hub_view(cog, requester=None):
    balance = None
    if requester is not None:
        balance = await asyncio.to_thread(cog.get_balance, requester.id)
    return CurrencyHubLayout(cog, requester, balance=balance)


# --- UI VIEWS ---
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
                    f"رصيدك الحالي **{fmt(balance_after)} {COIN_EMOJI}**، ومحتاج **{fmt(total_cost)} {COIN_EMOJI}** لشراء {quantity} × {self.item_name}.",
                    "warning",
                ),
                ephemeral=True,
            )

        await interaction.followup.send(
            view=StatusPanel(
                "تم الشراء",
                f"اشتريت **{quantity} × {self.item_name}** بمبلغ **{fmt(total_cost)} {COIN_EMOJI}**.\nرصيدك الجديد: **{fmt(balance_after)} {COIN_EMOJI}**.",
                "success",
            ),
            ephemeral=True,
        )
class ShopDropdown(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        self.prices = {item[0]: item[3] for item in INVENTORY_ITEMS}
        self.names = {item[0]: f"{item[5]} {item[1]}" for item in INVENTORY_ITEMS}
        options = [
            discord.SelectOption(
                label=name,
                description=f"{fmt(price)} عملة | {description}",
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
        if owner is not None and balance is None:
            raise ValueError("ShopView requires an asynchronously loaded balance snapshot")
        balance_line = (
            f"**رصيدك الحالي:** {fmt(balance)} {COIN_EMOJI}\n"
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
        row.add_item(ShopDropdown(cog))
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
                f"السعر في المتجر: **{fmt(price)}** {COIN_EMOJI}"
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
        balance_line = f"\n\n**الرصيد الحالي:** {fmt(self.balance)} {COIN_EMOJI}"
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
            notice = f"🎁 فتحت صندوق الحظ وكسبت **{fmt(reward)}** {COIN_EMOJI}."
        else:
            notice = f"🎟️ استخدمت تذكرة الحظ وكسبت **{fmt(reward)}** {COIN_EMOJI}."
        notice += f"\nرصيدك الجديد: **{fmt(balance_after)}** {COIN_EMOJI}"
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

class TransferModal(discord.ui.Modal, title="💸 تحويل العملات"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.target = discord.ui.TextInput(label="العضو أو المنشن", placeholder="مثال: @عضو", required=True)
        self.amount = discord.ui.TextInput(label="المبلغ", placeholder="100", required=True)
        self.add_item(self.target)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                view=StatusPanel("مبلغ غير صالح", "اكتب مبلغًا صحيحًا أكبر من صفر.", "warning"),
                ephemeral=True,
            )

        if amount <= 0:
            return await interaction.response.send_message(
                view=StatusPanel("مبلغ غير صالح", "المبلغ لازم يكون أكبر من صفر.", "warning"),
                ephemeral=True,
            )

        target_text = self.target.value.strip()
        match = re.fullmatch(r"<@!?(\d{17,20})>|(\d{17,20})", target_text)
        if match is None:
            return await interaction.response.send_message(
                view=StatusPanel("مستلم غير صالح", "منشن عضوًا من السيرفر أو اكتب الـID الصحيح.", "warning"),
                ephemeral=True,
            )
        target_id = int(match.group(1) or match.group(2))
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                view=StatusPanel("التحويل غير متاح", "التحويل متاح داخل السيرفر فقط.", "warning"),
                ephemeral=True,
            )
        if target_id == interaction.user.id:
            return await interaction.response.send_message(
                view=StatusPanel("تحويل غير صالح", "مينفعش تحول لنفسك.", "warning"),
                ephemeral=True,
            )

        # Acknowledge before the uncached-member API lookup so this modal cannot expire.
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except (discord.HTTPException, discord.InteractionResponded):
            logger.warning(
                "Could not acknowledge currency transfer interaction user_id=%s recipient_id=%s",
                interaction.user.id,
                target_id,
                exc_info=True,
            )
            return

        recipient = guild.get_member(target_id)
        if recipient is None:
            try:
                recipient = await guild.fetch_member(target_id)
            except discord.NotFound:
                return await interaction.followup.send(
                    view=StatusPanel("المستلم غير موجود", "العضو ده مش موجود في السيرفر.", "warning"),
                    ephemeral=True,
                )
            except discord.HTTPException:
                return await interaction.followup.send(
                    view=StatusPanel("تعذر التحقق", "مقدرناش نتحقق من العضو دلوقتي. حاول تاني.", "warning"),
                    ephemeral=True,
                )
        if recipient.bot:
            return await interaction.followup.send(
                view=StatusPanel("تحويل غير متاح", "التحويل لحسابات البوتات غير متاح.", "warning"),
                ephemeral=True,
            )

        try:
            transferred, balance_after = await asyncio.to_thread(
                bank.transfer_balance,
                interaction.user.id,
                recipient.id,
                amount,
            )
        except Exception:
            logger.exception(
                "Currency transfer transaction failed sender_id=%s recipient_id=%s",
                interaction.user.id,
                recipient.id,
            )
            return await interaction.followup.send(
                view=StatusPanel(
                    "تعذر تأكيد التحويل",
                    "حصل عطل أثناء معالجة التحويل. اتأكد من الرصيد قبل إعادة المحاولة؛ ما هنعتبرش العملية ناجحة من غير تأكيد قاعدة البيانات.",
                    "warning",
                ),
                ephemeral=True,
            )

        if not transferred:
            return await interaction.followup.send(
                view=StatusPanel(
                    "رصيد غير كافٍ",
                    f"رصيدك الحالي **{fmt(balance_after)} {COIN_EMOJI}** ولا يكفي لتحويل **{fmt(amount)} {COIN_EMOJI}**.",
                    "warning",
                ),
                ephemeral=True,
            )

        await interaction.followup.send(
            view=StatusPanel(
                "تم التحويل بنجاح",
                f"حوّلت **{fmt(amount)} {COIN_EMOJI}** إلى {recipient.mention}.\nرصيدك الجديد: **{fmt(balance_after)} {COIN_EMOJI}**.",
                "success",
            ),
            ephemeral=True,
        )
        try:
            await self.cog.log_action(
                interaction.guild,
                "💸 Transfer",
                f"From {interaction.user.mention} to {recipient.mention}: {fmt(amount)}",
                discord.Color.orange(),
            )
        except Exception:
            logger.exception(
                "Transfer committed but its audit-log notification failed sender_id=%s recipient_id=%s",
                interaction.user.id,
                recipient.id,
            )
class CurrencyHubLayout(LayoutView):
    def __init__(self, cog, requester=None, *, balance=None):
        super().__init__(timeout=None)
        self.cog = cog

        balance_line = "افتح خزنتك وتابع رصيدك وأدِر أغراضك من مكان واحد."
        if requester is not None:
            if balance is None:
                raise ValueError("CurrencyHubLayout requires an asynchronously loaded balance snapshot")
            balance_line = f"**الرصيد الحالي:** {fmt(balance)} {COIN_EMOJI}"

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
        await private_status(
            interaction,
            "استلمت اليومية",
            f"المكافأة: **{fmt(amount)} {COIN_EMOJI}**\nالستريك الحالي: **{streak}** يوم.",
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
        description = "\n".join(
            f"**#{index + 1}** <@{user_id}> — **{fmt(balance)}** {COIN_EMOJI}"
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
