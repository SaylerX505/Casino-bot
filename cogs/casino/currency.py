import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay
import json
import os
import random
import time
import re
import config
from cogs.casino import bank as bank
from cogs.utility.theme import StatusPanel, base_embed, THEME

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
        purchased, balance_after = bank.purchase_item(
            interaction.user.id, self.item_id, quantity, self.price
        )
        if not purchased:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "تعذر الشراء",
                    f"رصيدك الحالي **{fmt(balance_after)} {COIN_EMOJI}**، ومحتاج **{fmt(total_cost)} {COIN_EMOJI}** لشراء {quantity} × {self.item_name}.",
                    "warning",
                ),
                ephemeral=True,
            )

        await interaction.response.send_message(
            view=StatusPanel(
                "تم الشراء",
                f"اشتريت **{quantity} × {self.item_name}** بمبلغ **{fmt(total_cost)} {COIN_EMOJI}**.\nرصيدك الجديد: **{fmt(self.cog.get_balance(interaction.user.id))} {COIN_EMOJI}**.",
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


class ShopView(LayoutView):
    def __init__(self, cog):
        super().__init__(timeout=180)
        container = Container(
            TextDisplay(
                "# 🛒 دكان الحفرة\n"
                "اختار الغرض اللي محتاجه، راجع سعره، وحدد الكمية قبل الشراء.\n"
                "عملية الشراء بتتأكد من الرصيد وتضيف الغرض بشكل ذري."
            ),
            Separator(),
            accent_color=config.EMBED_COLOR,
        )
        row = ActionRow()
        row.add_item(ShopDropdown(cog))
        container.add_item(row)
        self.add_item(container)

class InventoryView(LayoutView):
    def __init__(self, cog, owner, category="all", notice=None):
        super().__init__(timeout=180)
        self.cog = cog
        self.owner = owner
        self.owner_id = owner.id
        self.category = category
        self.notice = notice
        self.message = None

        container = Container(
            TextDisplay(self.make_description()),
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
            emoji="🎁",
            style=discord.ButtonStyle.secondary,
            custom_id="inventory_use_box",
            disabled=self.cog.get_item_count(self.owner_id, "loot_box") < 1,
        )
        self.box_button.callback = self.use_box_button
        use_row.add_item(self.box_button)
        self.ticket_button = discord.ui.Button(
            label="استخدم تذكرة",
            emoji="🎟️",
            style=discord.ButtonStyle.secondary,
            custom_id="inventory_use_ticket",
            disabled=self.cog.get_item_count(self.owner_id, "luck_ticket") < 1,
        )
        self.ticket_button.callback = self.use_ticket_button
        use_row.add_item(self.ticket_button)
        container.add_item(use_row)
        self.add_item(container)

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
        self.box_button.disabled = inventory.get("loot_box", 0) < 1
        self.ticket_button.disabled = inventory.get("luck_ticket", 0) < 1

    def make_description(self, notice=None):
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

        current_notice = self.notice if notice is None else notice
        if current_notice:
            description = f"{current_notice}\n\n{description}"
        balance_line = f"\n\n**الرصيد الحالي:** {fmt(self.cog.get_balance(self.owner_id))} {COIN_EMOJI}"
        return f"# 🎒 شنطة {self.owner.display_name}\n{description}{balance_line}"

    def make_embed(self, notice=None):
        return discord.Embed(
            title=f"🎒 شنطة {self.owner.display_name}",
            description=self.make_description(notice),
            color=discord.Color.dark_purple(),
        )

    async def select_category(self, interaction: discord.Interaction):
        view = InventoryView(
            self.cog,
            self.owner,
            category=self.category_select.values[0],
            notice=self.notice,
        )
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()

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

        if item_id == "loot_box":
            notice = f"🎁 فتحت صندوق الحظ وكسبت **{fmt(reward)}** {COIN_EMOJI}."
        else:
            notice = f"🎟️ استخدمت تذكرة الحظ وكسبت **{fmt(reward)}** {COIN_EMOJI}."
        notice += f"\nرصيدك الجديد: **{fmt(balance_after)}** {COIN_EMOJI}"
        view = InventoryView(self.cog, self.owner, category=self.category, notice=notice)
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()

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

        recipient = guild.get_member(target_id)
        if recipient is None:
            try:
                recipient = await guild.fetch_member(target_id)
            except discord.NotFound:
                return await interaction.response.send_message(
                    view=StatusPanel("المستلم غير موجود", "العضو ده مش موجود في السيرفر.", "warning"),
                    ephemeral=True,
                )
            except discord.HTTPException:
                return await interaction.response.send_message(
                    view=StatusPanel("تعذر التحقق", "مقدرناش نتحقق من العضو دلوقتي. حاول تاني.", "warning"),
                    ephemeral=True,
                )
        if recipient.bot:
            return await interaction.response.send_message(
                view=StatusPanel("تحويل غير متاح", "التحويل لحسابات البوتات غير متاح.", "warning"),
                ephemeral=True,
            )

        transferred, balance_after = bank.transfer_balance(interaction.user.id, recipient.id, amount)
        if not transferred:
            return await interaction.response.send_message(
                view=StatusPanel(
                    "رصيد غير كافٍ",
                    f"رصيدك الحالي **{fmt(balance_after)} {COIN_EMOJI}** ولا يكفي لتحويل **{fmt(amount)} {COIN_EMOJI}**.",
                    "warning",
                ),
                ephemeral=True,
            )

        await interaction.response.send_message(
            view=StatusPanel(
                "تم التحويل بنجاح",
                f"حوّلت **{fmt(amount)} {COIN_EMOJI}** إلى {recipient.mention}.\nرصيدك الجديد: **{fmt(balance_after)} {COIN_EMOJI}**.",
                "success",
            ),
            ephemeral=True,
        )
        await self.cog.log_action(
            "💸 Transfer",
            f"From {interaction.user.mention} to {recipient.mention}: {fmt(amount)}",
            discord.Color.orange(),
        )
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
        self._add_button(first_row, "اليومية", "🎁", discord.ButtonStyle.secondary, "hubv2_daily", self.daily_btn)
        self._add_button(first_row, "الأغنى", "🏆", discord.ButtonStyle.secondary, "hubv2_top", self.top_btn)
        self._add_button(first_row, "المتجر", "🛒", discord.ButtonStyle.secondary, "hubv2_shop", self.shop_btn)
        container.add_item(first_row)

        second_row = ActionRow()
        self._add_button(second_row, "تحويل", "💸", discord.ButtonStyle.secondary, "hubv2_transfer", self.transfer_btn)
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
                    view=StatusPanel("المكافأة غير متاحة", "رصيدك وصل للحد الأقصى المدعوم؛ المكافأة لم تُصرف ولم تُستهلك مطالبتك اليومية.", "warning"),
                    ephemeral=True,
                )
            next_available_time = int(claim["last_daily"] + 86400)
            return await interaction.response.send_message(
                view=StatusPanel("اليومية مستلمة بالفعل", f"تقدر ترجع تطالب بيها <t:{next_available_time}:R>.", "warning"),
                ephemeral=True,
            )

        amount = claim["amount"]
        streak = claim["streak"]
        await interaction.response.send_message(
            view=StatusPanel(
                "استلمت اليومية",
                f"المكافأة: **{fmt(amount)} {COIN_EMOJI}**\nالستريك الحالي: **{streak}** يوم.",
                "success",
            ),
            ephemeral=True,
        )
    async def top_btn(self, interaction: discord.Interaction):
        ranking = bank.top_balances(10)
        description = "\n".join(
            f"**#{index + 1}** <@{user_id}> — **{fmt(balance)}** {COIN_EMOJI}"
            for index, (user_id, balance) in enumerate(ranking)
        )
        await interaction.response.send_message(
            view=StatusPanel("🏆 أغنى الأعضاء", description or "الخزنة فاضية لسه!"),
            ephemeral=True,
        )
    async def shop_btn(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=ShopView(self.cog), ephemeral=True)

    async def transfer_btn(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TransferModal(self.cog))

class Currency(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()
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

    def debit_balance(self, user_id: int, amount: int):
        return bank.debit_balance(user_id, amount)

    def charge_up_to(self, user_id: int, amount: int):
        return bank.charge_up_to(user_id, amount)

    def settle_wheel(self, user_id: int, payout: int = 0, target_loss: int = None):
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
    async def use_item(self, ctx, item: str = None):
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
