import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay
import json
import os
from typing import Union
import random
import time
import re
import config
from cogs.casino import bank as bank
from cogs.utility.theme import base_embed, THEME

COIN_EMOJI = "<:ckrcoin:1513743032124575784>"
LOG_CHANNEL_ID = 1513736075120742610
SERVER_LOGO = "https://media.discordapp.net/attachments/1509650275164749994/1513741255786627162/c51bafdd9c1feb13e22458c35067c718.webp"
DATA_FILE = "data/currency.json"

INVENTORY_ITEMS = (
    ("double_shot", "طلقة مزدوجة", "tools", 100, "تسمح باستهداف لاعب إضافي في الروليت.", "🔫"),
    ("shield", "درع الحفرة", "tools", 275, "يصد الاستهداف في الروليت ويردّه على المهاجم.", "🛡️"),
    ("mafia_scan", "كشف مافيا", "tools", 150, "يوفر تلميحًا عن أحد أفراد المافيا أثناء اللعبة.", "🔍"),
    ("veto", "فيتو", "tools", 200, "يلغي صوتًا ضدك أثناء تصويت المافيا.", "⛔"),
    ("roulette_ins", "تأمين روليت", "tools", 300, "يمنح فرصة نجاة إضافية في الروليت.", "💉"),
    ("luck_ticket", "تذكرة حظ", "consumables", 120, "استخدمها لتحصل على مكافأة عشوائية حتى 150.", "🎟️"),
    ("loot_box", "صندوق حظ", "consumables", 250, "افتحه لتحصل على مكافأة عشوائية حتى 400.", "🎁"),
)

OWNER_IDS = [1446557924834017441, 1135291623840948375, 853670651428077609, 798280308071596063, 1159601661392715906, 1028268695887282187]
ALLOWED_CHANNEL_ID = 1521263741520314481

def is_allowed_channel_or_owner():
    async def predicate(ctx):
        if ctx.author.id in OWNER_IDS:
            return True
        if ctx.channel.id == ALLOWED_CHANNEL_ID:
            return True
        # --- DEFAULT DENIAL ---
        return False
    return commands.check(predicate)

def load_data():
    # --- LEGACY JSON DATA ---
    os.makedirs("data", exist_ok=True)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(data):
    # --- LEGACY API COMPATIBILITY ---
    return

def fmt(n) -> str:
    return bank.fmt(n)

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
            embed = discord.Embed(
                description="❌ اكتب رقم صحيح للكمية.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if quantity <= 0:
            embed = discord.Embed(
                description="❌ الكمية لازم تكون 1 على الأقل.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        total_cost = quantity * self.price
        purchased, balance_after = bank.purchase_item(
            interaction.user.id, self.item_id, quantity, self.price
        )
        if not purchased:
            embed = discord.Embed(
                description=(
                    f"❌ رصيدك الحالي {fmt(balance_after)} "
                    f"{COIN_EMOJI}; محتاج {fmt(total_cost)} علشان تشتري "
                    f"{quantity} × {self.item_name}."
                ),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        embed = discord.Embed(
            description=f"✅ اشتريت **{quantity} × {self.item_name}** بمبلغ {fmt(total_cost)} {COIN_EMOJI}.\n\nرصيدك الجديد **{fmt(self.cog.get_balance(interaction.user.id))} {COIN_EMOJI}**.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ShopDropdown(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        self.prices = {item[0]: item[3] for item in INVENTORY_ITEMS}
        self.names = {item[0]: f"{item[5]} {item[1]}" for item in INVENTORY_ITEMS}
        options = [
            discord.SelectOption(
                label=name,
                description=f"{price} | {description}",
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


class ShopView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180)
        self.add_item(ShopDropdown(cog))


class InventoryCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="كل الأغراض", value="all", emoji="🎒"),
            discord.SelectOption(label="أدوات الألعاب", value="tools", emoji="🎮"),
            discord.SelectOption(label="المستهلكات", value="consumables", emoji="🎁"),
        ]
        super().__init__(
            placeholder="اختار قسم الشنطة...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.category = self.values[0]
        self.view.refresh_buttons()
        await interaction.response.edit_message(
            embed=self.view.make_embed(),
            view=self.view,
        )


class InventoryView(discord.ui.View):
    def __init__(self, cog, owner):
        super().__init__(timeout=180)
        self.cog = cog
        self.owner = owner
        self.owner_id = owner.id
        self.category = "all"
        self.message = None
        self.add_item(InventoryCategorySelect())
        self.refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "الشنطة دي شخصية لصاحبها؛ افتح +شنطتي عشان تستخدم أغراضك.",
                ephemeral=True,
            )
            return False
        return True

    def refresh_buttons(self):
        inventory = self.cog.get_inventory(self.owner_id)
        for child in self.children:
            if child.custom_id == "inventory_use_box":
                child.disabled = inventory.get("loot_box", 0) < 1
            elif child.custom_id == "inventory_use_ticket":
                child.disabled = inventory.get("luck_ticket", 0) < 1

    def make_embed(self, notice=None):
        inventory = self.cog.get_inventory(self.owner_id)
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

        if notice:
            description = f"{notice}\n\n{description}"

        embed = discord.Embed(
            title=f"🎒 شنطة {self.owner.display_name}",
            description=description,
            color=discord.Color.dark_purple(),
        )
        embed.set_thumbnail(url=self.owner.display_avatar.url)
        embed.add_field(
            name="💰 الرصيد الحالي",
            value=f"**{fmt(self.cog.get_balance(self.owner_id))}** {COIN_EMOJI}",
            inline=False,
        )
        embed.set_footer(text="اختار قسم من القائمة، أو استخدم الصندوق والتذكرة مباشرة من هنا.")
        return embed

    async def redeem_consumable(self, interaction: discord.Interaction, item_id: str):
        redeemed, reward, balance_after = self.cog.redeem_consumable(self.owner_id, item_id)
        if not redeemed:
            if self.cog.get_item_count(self.owner_id, item_id) > 0:
                return await interaction.response.send_message(
                    "رصيدك وصل للحد الأقصى؛ احتفظنا بالغرض في شنطتك ولم نستهلكه.",
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                "الغرض ده مش موجود في شنطتك. افتح المتجر من مركز الاقتصاد لشرائه.",
                ephemeral=True,
            )

        self.refresh_buttons()
        if item_id == "loot_box":
            result = f"🎁 فتحت صندوق الحظ وكسبت **{fmt(reward)}** {COIN_EMOJI}."
        else:
            result = f"🎟️ استخدمت تذكرة الحظ وكسبت **{fmt(reward)}** {COIN_EMOJI}."
        result += f"\nرصيدك الجديد: **{fmt(balance_after)}** {COIN_EMOJI}"
        await interaction.response.edit_message(embed=self.make_embed(notice=result), view=self)

    @discord.ui.button(
        label="استخدم صندوق",
        emoji="🎁",
        style=discord.ButtonStyle.success,
        custom_id="inventory_use_box",
        row=1,
    )
    async def use_box_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.redeem_consumable(interaction, "loot_box")

    @discord.ui.button(
        label="استخدم تذكرة",
        emoji="🎟️",
        style=discord.ButtonStyle.primary,
        custom_id="inventory_use_ticket",
        row=1,
    )
    async def use_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.redeem_consumable(interaction, "luck_ticket")

    async def on_timeout(self):
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

class TransferModal(discord.ui.Modal, title="💸 تحويل العملات"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.target = discord.ui.TextInput(label="العضو أو المنشن", placeholder="مثال: @عضو", required=True)
        self.amount = discord.ui.TextInput(label="المبلغ", placeholder="100", required=True)
        self.add_item(self.target)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try: amt = int(self.amount.value.strip())
        except:
            embed = discord.Embed(
                description="❌ المبلغ لازم يكون رقم صحيح.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if amt <= 0:
            embed = discord.Embed(
                description="❌ المبلغ لازم يكون أكبر من صفر.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        nums = re.findall(r'\d+', self.target.value)
        if not nums:
            embed = discord.Embed(
                description="❌ منشن العضو أو الـID غير صحيح.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        target_id = int(nums[0])
        
        if target_id == interaction.user.id:
            embed = discord.Embed(
                description="❌ مينفعش تحول لنفسك.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        transferred, balance_after = bank.transfer_balance(interaction.user.id, target_id, amt)
        if not transferred:
            embed = discord.Embed(
                description=f"❌ رصيدك مش كفاية. رصيدك الحالي {fmt(balance_after)} {COIN_EMOJI}.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        embed = discord.Embed(
            description=f"✅ تم تحويل **{fmt(amt)}** {COIN_EMOJI} إلى <@{target_id}> بنجاح.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.cog.log_action("💸 Transfer", f"From {interaction.user.mention} to <@{target_id}>: {fmt(amt)}", discord.Color.orange())

class AdminActionModal(discord.ui.Modal):
    def __init__(self, cog, action):
        titles = {"add": "➕ إضافة رصيد", "remove": "➖ خصم رصيد", "reset": "⚠️ تصفير الرصيد"}
        super().__init__(title=titles[action])
        self.cog = cog
        self.action = action
        self.target = discord.ui.TextInput(label="ID العضو", required=True)
        self.add_item(self.target)
        if action != "reset":
            self.amount = discord.ui.TextInput(label="المبلغ", required=True)
            self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        nums = re.findall(r'\d+', self.target.value)
        if not nums:
            embed = discord.Embed(
                description="❌ الـID غير صحيح.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        target_id = int(nums[0])
        
        if self.action == "reset":
            bank.set_balance(target_id, 0)
            embed = discord.Embed(
                description=f"✅ تم تصفير رصيد <@{target_id}>!",
                color=discord.Color.green()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        try: amt = int(self.amount.value)
        except:
            embed = discord.Embed(
                description="❌ المبلغ لازم يكون رقم صحيح.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if amt <= 0:
            embed = discord.Embed(
                description="❌ المبلغ لازم يكون أكبر من صفر.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if self.action == "add":
            credited, balance_after = bank.credit_balance(target_id, amt)
            if not credited:
                embed = discord.Embed(
                    description="❌ المبلغ أكبر من الحد المدعوم للرصيد.",
                    color=discord.Color.red()
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            embed = discord.Embed(
                description=f"✅ أضفنا {fmt(amt)} إلى <@{target_id}>. الرصيد الجديد: {fmt(balance_after)} {COIN_EMOJI}.",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif self.action == "remove":
            removed, balance_after = bank.remove_balance(target_id, amt)
            if not removed:
                embed = discord.Embed(
                    description=f"❌ مينفعش تخصم {fmt(amt)}؛ الرصيد الحالي {fmt(balance_after)} {COIN_EMOJI}.",
                    color=discord.Color.red()
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            embed = discord.Embed(
                description=f"✅ تم خصم {fmt(amt)} من <@{target_id}>",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class AdminActionView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180)
        self.cog = cog
        
    @discord.ui.button(label="إضافة عملات", style=discord.ButtonStyle.success)
    async def add_btn(self, interaction, button): await interaction.response.send_modal(AdminActionModal(self.cog, "add"))
    @discord.ui.button(label="خصم عملات", style=discord.ButtonStyle.danger)
    async def rem_btn(self, interaction, button): await interaction.response.send_modal(AdminActionModal(self.cog, "remove"))
    @discord.ui.button(label="تصفير", style=discord.ButtonStyle.secondary)
    async def res_btn(self, interaction, button): await interaction.response.send_modal(AdminActionModal(self.cog, "reset"))

class CurrencyHubLayout(LayoutView):
    def __init__(self, cog, requester=None):
        super().__init__(timeout=None)
        self.cog = cog

        balance_line = "افتح خزنتك وتابع رصيدك وأدِر أغراضك من مكان واحد."
        if requester is not None:
            balance_line = f"**الرصيد الحالي:** {fmt(cog.get_balance(requester.id))} {COIN_EMOJI}"

        container = Container(
            TextDisplay(
                f"# 🏦 خزنة الحفرة\n{balance_line}\n\n"
                "اختار العملية اللي محتاجها. نتائج العمليات الحساسة بتظهر لك بشكل خاص."
            ),
            Separator(),
            accent_color=config.EMBED_COLOR,
        )

        first_row = ActionRow()
        self._add_button(first_row, "اليومية", "🎁", discord.ButtonStyle.success, "hubv2_daily", self.daily_btn)
        self._add_button(first_row, "الأغنى", "🏆", discord.ButtonStyle.secondary, "hubv2_top", self.top_btn)
        self._add_button(first_row, "المتجر", "🛒", discord.ButtonStyle.primary, "hubv2_shop", self.shop_btn)
        container.add_item(first_row)

        second_row = ActionRow()
        self._add_button(second_row, "تحويل", "💸", discord.ButtonStyle.primary, "hubv2_transfer", self.transfer_btn)
        if requester is None or cog.check_whitelist(requester):
            self._add_button(second_row, "إدارة البنك", "⚙️", discord.ButtonStyle.danger, "hubv2_admin", self.admin_btn)
        container.add_item(second_row)
        self.add_item(container)

    @staticmethod
    def _add_button(row, label, emoji, style, custom_id, callback):
        button = discord.ui.Button(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=custom_id,
        )
        button.callback = callback
        row.add_item(button)

    async def daily_btn(self, interaction: discord.Interaction):
        claim = bank.claim_daily(interaction.user.id, bank.now(), random.randint(20, 45))
        if not claim["claimed"]:
            if claim.get("reason") == "balance_limit":
                return await interaction.response.send_message(
                    "❌ رصيدك وصل للحد الأقصى المدعوم؛ المكافأة لم تُصرف ولم تُستهلك مطالبتك اليومية.",
                    ephemeral=True,
                )
            next_available_time = int(claim["last_daily"] + 86400)
            embed = discord.Embed(
                description=f"❌ أخدت اليومية بالفعل! ارجع <t:{next_available_time}:R>",
                color=discord.Color.red(),
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        amount = claim["amount"]
        streak = claim["streak"]
        embed = discord.Embed(
            description=(
                f"✅ استلمت اليومية: {fmt(amount)} {COIN_EMOJI}\n"
                f"🔥 ستريك **{streak}** يوم (بونص +{fmt(min(streak, 7) * 10)} بكرة)"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def top_btn(self, interaction: discord.Interaction):
        ranking = bank.top_balances(10)
        description = "\n".join(
            f"**#{index + 1}** <@{user_id}> ➔ **{fmt(balance)}** {COIN_EMOJI}"
            for index, (user_id, balance) in enumerate(ranking)
        )
        embed = discord.Embed(
            title="🏆 أغنى الأعضاء",
            description=description or "الخزنة فاضية لسه!",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def shop_btn(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=ShopView(self.cog), ephemeral=True)

    async def transfer_btn(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TransferModal(self.cog))

    async def admin_btn(self, interaction: discord.Interaction):
        if not self.cog.check_whitelist(interaction.user):
            return await interaction.response.send_message(
                "❌ معندكش صلاحية للوصول للوحة دي.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "لوحة إدارة البنك:",
            view=AdminActionView(self.cog),
            ephemeral=True,
        )

class CurrencyHubView(discord.ui.View):
    def __init__(self, cog, requester=None):
        super().__init__(timeout=None)
        self.cog = cog

        # --- ADMIN PANEL VISIBILITY ---
        if requester is not None and not cog.check_whitelist(requester):
            admin_button = next(
                (
                    child
                    for child in self.children
                    if getattr(child, "custom_id", None) == "hub_admin"
                ),
                None,
            )
            if admin_button is not None:
                self.remove_item(admin_button)
        
    @discord.ui.button(label="اليومية", style=discord.ButtonStyle.success, emoji="🎁", custom_id="hub_daily")
    async def daily_btn(self, interaction, button):
        now = bank.now()
        claim = bank.claim_daily(interaction.user.id, now, random.randint(20, 45))
        if not claim["claimed"]:
            if claim.get("reason") == "balance_limit":
                return await interaction.response.send_message(
                    "❌ رصيدك وصل للحد الأقصى المدعوم؛ المكافأة لم تُصرف ولم تُستهلك مطالبتك اليومية.",
                    ephemeral=True,
                )
            next_available_time = int(claim["last_daily"] + 86400)
            embed = discord.Embed(
                description=f"❌ أخدت اليومية بالفعل! ارجع <t:{next_available_time}:R>",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        amt = claim["amount"]
        streak = claim["streak"]
        embed = discord.Embed(
            description=f"✅ استلمت اليومية: {fmt(amt)} {COIN_EMOJI}\n🔥 ستريك **{streak}** يوم (بونص +{fmt(min(streak, 7) * 10)} بكرة)",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="الأغنى", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="hub_top")
    async def top_btn(self, interaction, button):
        srt = bank.top_balances(10)
        desc = "\n".join([f"**#{i+1}** <@{u}> ➔ **{fmt(b)}** {COIN_EMOJI}" for i, (u, b) in enumerate(srt)])
        embed = discord.Embed(
            title="🏆 أغنى الأعضاء",
            description=desc or "الخزنة فاضية لسه!",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    @discord.ui.button(label="المتجر", style=discord.ButtonStyle.primary, emoji="🛒", custom_id="hub_shop")
    async def shop_btn(self, interaction, button):
        await interaction.response.send_message(view=ShopView(self.cog), ephemeral=True)

    @discord.ui.button(label="تحويل", style=discord.ButtonStyle.primary, emoji="💸", custom_id="hub_transfer")
    async def transfer_btn(self, interaction, button):
        await interaction.response.send_modal(TransferModal(self.cog))
        
    @discord.ui.button(label="إدارة البنك", style=discord.ButtonStyle.danger, emoji="⚙️", custom_id="hub_admin")
    async def admin_btn(self, interaction, button):
        if not self.cog.check_whitelist(interaction.user):
            embed = discord.Embed(
                description="❌ معندكش صلاحية للوصول للوحة دي.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_message("لوحة إدارة البنك:", view=AdminActionView(self.cog), ephemeral=True)

# --- BANK RESET CONFIRMATION ---
class BankResetConfirmView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=30)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ التأكيد متاح لمالك البوت اللي بدأ العملية بس.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="تأكيد التصفير", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        backup_path = os.path.join("data", "backups", f"bank-reset-{time.time_ns()}.db")
        try:
            bank.backup_database(backup_path)
            bank.reset_all()
        except Exception:
            import traceback
            traceback.print_exc()
            for item in self.children:
                item.disabled = True
            try:
                await interaction.message.edit(
                    embed=discord.Embed(
                        title="❌ التصفير لم يكتمل",
                        description="حصل خطأ. راجع سجل البوت؛ متعتبرش العملية ناجحة.",
                        color=discord.Color.red(),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass
            return await interaction.followup.send("فشل التصفير أو النسخ الاحتياطي؛ راجع سجل البوت قبل أي محاولة تانية.", ephemeral=True)

        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(
                embed=discord.Embed(
                    title="✅ تم تصفير البنك",
                    description="اتمسحت الأرصدة والشنط بعد حفظ نسخة احتياطية.",
                    color=discord.Color.green(),
                ),
                view=self,
            )
        except discord.HTTPException:
            pass
        await interaction.followup.send("تم حفظ النسخة الاحتياطية قبل التصفير.", ephemeral=True)

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="تم الإلغاء",
                description="البنك زي ما هو؛ مفيش أرصدة اتصفرت.",
                color=discord.Color.greyple(),
            ),
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=discord.Embed(
                        title="انتهى وقت التأكيد",
                        description="البنك لم يتغير.",
                        color=discord.Color.greyple(),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


# --- CURRENCY COG ---
class Currency(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    def check_whitelist(self, user: discord.Member) -> bool:
        permissions = getattr(user, "guild_permissions", None)
        if getattr(permissions, "administrator", False) or user.id in OWNER_IDS:
            return True
        wl = bank.get_whitelist()
        if user.id in wl["users"]:
            return True
        user_role_ids = [role.id for role in getattr(user, "roles", ())]
        if any(role_id in wl["roles"] for role_id in user_role_ids):
            return True
        return False

    def update_balance(self, user_id: int, amount: int):
        return bank.update_balance(user_id, amount)

    def debit_balance(self, user_id: int, amount: int):
        return bank.debit_balance(user_id, amount)

    def charge_up_to(self, user_id: int, amount: int):
        return bank.charge_up_to(user_id, amount)

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

    @commands.hybrid_command(name='bankreset', aliases=['تصفير'], description='تصفير الاقتصاد بعد إنشاء نسخة احتياطية')
    @commands.is_owner()
    async def bank_reset(self, ctx):
        view = BankResetConfirmView(ctx.author.id)
        embed = discord.Embed(
            title="⚠️ تأكيد تصفير البنك",
            description="العملية هتمسح أرصدة الأعضاء والشنط. مش هننفذها غير بعد إنشاء نسخة احتياطية وتأكيدك.",
            color=discord.Color.red(),
        )
        if ctx.interaction is not None:
            view.message = await ctx.send(embed=embed, view=view, ephemeral=True)
        else:
            view.message = await ctx.send(embed=embed, view=view)

    @commands.command(name='bankmigrate', aliases=['ترحيل'])
    @commands.is_owner()
    async def bank_migrate(self, ctx):
        """One-time import from old currency.json into SQLite."""
        data = load_data()
        if not data:
            return await ctx.send("❌ مفيش currency.json عشان ارحله!")
        counts = bank.migrate_from_json(data)
        await ctx.send(f"✅ اترحل! يوزر: **{counts['users']}** / اصناف: **{counts['items']}** / وايت ليست: **{counts['wl']}**")

    async def log_action(self, title: str, description: str, color: discord.Color):
        channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.set_footer(text=f"Bank Log • {config.BRAND}")
            embed.set_thumbnail(url=SERVER_LOGO)
            try: await channel.send(embed=embed)
            except: pass

        @commands.hybrid_command(name='bal', aliases=['balance', 'رصيد', 'فلوسي', 'محفظتي'], description='عرض رصيدك وفتح مركز الاقتصاد')
    async def balance(self, ctx):
        view = CurrencyHubLayout(self, ctx.author)
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        await ctx.send(view=view, **send_kwargs)

    @commands.hybrid_command(name='inv', aliases=['inventory', 'شنطتي', 'حقيبتي'], description='عرض أغراضك في الشنطة')
    async def inventory(self, ctx):
        view = InventoryView(self, ctx.author)
        send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
        view.message = await ctx.send(embed=view.make_embed(), view=view, **send_kwargs)
    @commands.hybrid_command(name='بروفايل', aliases=['profile', 'me', 'انا'], description='عرض بروفايلك أو بروفايل عضو')
    async def profile(self, ctx, target: discord.Member = None):
        """كارت البروفايل بتاعك: رصيد + ستريك + اغراض"""
        user = target or ctx.author
        bal = self.get_balance(user.id)
        _last, streak = bank.get_daily(str(user.id))
        inv = self.get_inventory(user.id)
        item_labels = {
            item_id: f"{emoji} {name}"
            for item_id, name, _category, _price, _description, emoji in INVENTORY_ITEMS
        }
        items_txt = " • ".join(
            f"{item_labels.get(item_id, item_id)} ×{fmt(quantity)}"
            for item_id, quantity in inv.items()
            if quantity > 0
        ) or "الشنطة فاضية حاليًا"

        embed = base_embed(
            title=f"🪪 بروفايل {user.display_name}",
            description="ملخص حسابك في الحفرة.",
            color=THEME["gold"],
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(
            name="💰 الرصيد",
            value=f"**{fmt(bal)}** {COIN_EMOJI}",
            inline=True,
        )
        embed.add_field(
            name="🔥 الستريك اليومي",
            value=f"**{streak}** يوم",
            inline=True,
        )
        embed.add_field(
            name="🎒 الأغراض المملوكة",
            value=items_txt[:1024],
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='استخدم', aliases=['use', 'افتح'], description='استخدام صندوق أو تذكرة حظ')
    async def use_item(self, ctx, item: str = None):
        """استخدم صندوق حظ او تذكرة: +استخدم صندوق / +استخدم تذكرة"""
        if not item:
            return await ctx.send("❌ اكتب اسم الصنف! مثال: `+استخدم صندوق` او `+استخدم تذكرة`")
        low = item.strip().lower()
        uid = str(ctx.author.id)
        if low in ("صندوق", "box", "loot", "loot_box"):
            key = "loot_box"
        elif low in ("تذكرة", "ticket", "luck", "luck_ticket"):
            key = "luck_ticket"
        else:
            return await ctx.send("❌ الصنف ده مابيستخدمش كده! الصناديق والتذاكر بس (`+استخدم صندوق`). الكشف بـ `+اكشف` في جيم المافيا، والفيتو والتأمين بيشتغلوا تلقائي.")
        redeemed, reward, balance_after = self.redeem_consumable(ctx.author.id, key)

        if not redeemed:
            if self.get_item_count(ctx.author.id, key) > 0:
                return await ctx.send(
                    "❌ رصيدك وصل للحد الأقصى المدعوم؛ احتفظنا بالصنف في شنطتك ولم نستهلكه."
                )
            return await ctx.send(
                f"❌ معكش **{key}**! اشتريه من الشوب (`+رصيد` > Shop)."
            )

        if key == "loot_box":
            desc = f"🎁 {ctx.author.mention} فتح صندوق الحظ وطلعله **{fmt(reward)}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{fmt(balance_after)}**"
        else:
            desc = f"🎟️ {ctx.author.mention} استخدم تذكرة الحظ وكسب **{fmt(reward)}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{fmt(balance_after)}**"
        await ctx.send(embed=discord.Embed(title="🍀 حظك!", description=desc, color=discord.Color.gold()))

    @commands.command(name='whitelist')
    @commands.is_owner()
    async def whitelist_bank(self, ctx, action: str, target: Union[discord.Member, discord.Role]):
        target_type = "users" if isinstance(target, discord.Member) else "roles"
        if action == "add":
            bank.whitelist_add(target_type, target.id)
            embed = discord.Embed(
                description=f"✅ Permission added to {target.mention}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        elif action == "remove":
            bank.whitelist_remove(target_type, target.id)
            embed = discord.Embed(
                description=f"✅ Permission removed from {target.mention}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

async def setup(bot):
    cog = Currency(bot)
    await bot.add_cog(cog)
    old_view = getattr(bot, "_currency_hub_view", None)
    if old_view is not None:
        bot.remove_view(old_view)
    view = CurrencyHubView(cog)
    bot.add_view(view)
    bot._currency_hub_view = view
    old_layout = getattr(bot, "_currency_hub_layout", None)
    if old_layout is not None:
        bot.remove_view(old_layout)
    layout = CurrencyHubLayout(cog)
    bot.add_view(layout)
    bot._currency_hub_layout = layout