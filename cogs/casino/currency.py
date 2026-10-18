import discord
from discord.ext import commands
import json
import os
from typing import Union
import random
import time
import re
import config
from cogs.casino import bank as bank

COIN_EMOJI = "<:ckrcoin:1513743032124575784>"
LOG_CHANNEL_ID = 1513736075120742610
SERVER_LOGO = "https://media.discordapp.net/attachments/1509650275164749994/1513741255786627162/c51bafdd9c1feb13e22458c35067c718.webp"
DATA_FILE = "data/currency.json"

OWNER_IDS = [1446557924834017441, 1135291623840948375, 853670651428077609, 798280308071596063, 1159601661392715906, 1028268695887282187]
ALLOWED_CHANNEL_ID = 1521263741520314481

def is_allowed_channel_or_owner():
    async def predicate(ctx):
        if ctx.author.id in OWNER_IDS:
            return True
        if ctx.channel.id == ALLOWED_CHANNEL_ID:
            return True
        # Fails silently if used outside the channel by a non-owner
        return False
    return commands.check(predicate)

def load_data():
    # Legacy JSON kept only for one-time migration. Live data lives in SQLite (bank.db).
    os.makedirs("data", exist_ok=True)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(data):
    # No-op: SQLite auto-commits. Kept so old calls don't crash during transition.
    return

def fmt(n) -> str:
    return bank.fmt(n)

# ==========================================
# 🛒 Modals & Views
# ==========================================
class BuyForm(discord.ui.Modal, title="Purchase Item"):
    def __init__(self, cog, item_id, price, item_name):
        super().__init__()
        self.cog = cog
        self.item_id = item_id
        self.price = price
        self.item_name = item_name
        self.add_item(discord.ui.TextInput(
            label="Quantity", 
            placeholder="Enter the number of items you want...", 
            required=True
        ))
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.children[0].value.strip())
        except ValueError:
            embed = discord.Embed(
                description="❌ Please enter a valid number for quantity.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if quantity <= 0:
            embed = discord.Embed(
                description="❌ Quantity must be at least 1.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        total_cost = quantity * self.price
        bal = self.cog.get_balance(interaction.user.id)
        
        if bal < total_cost:
            embed = discord.Embed(
                description=f"❌ Insufficient balance! You need {total_cost} coins for {quantity}x {self.item_name}.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        self.cog.update_balance(interaction.user.id, -total_cost)
        self.cog.add_item(interaction.user.id, self.item_id, quantity)
        embed = discord.Embed(
            description=f"✅ Success! You bought **{quantity}x {self.item_name}** for {fmt(total_cost)} {COIN_EMOJI}.\n\nYour new balance is **{fmt(self.cog.get_balance(interaction.user.id))} {COIN_EMOJI}**.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ShopDropdown(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        self.prices = {"double_shot": 100, "shield": 275, "mafia_scan": 150, "veto": 200, "roulette_ins": 300, "luck_ticket": 120, "loot_box": 250}
        self.names = {"double_shot": "Double Shot 🔫", "shield": "درع الحفره 🛡️", "mafia_scan": "كشف مافيا 🔍", "veto": "فيتو ⛔", "roulette_ins": "تأمين روليت 💉", "luck_ticket": "تذكرة حظ 🎟️", "loot_box": "صندوق حظ 🎁"}
        options = [
            discord.SelectOption(label="Double Shot 🔫", description="100 | اضرب اتنين في الروليت", value="double_shot", emoji="🔫"),
            discord.SelectOption(label="درع الحفره 🛡️", description="275 | صد الضربة ورجعها", value="shield", emoji="🛡️"),
            discord.SelectOption(label="كشف مافيا 🔍", description="150 | تلميح عن مافيا واحد", value="mafia_scan", emoji="🔍"),
            discord.SelectOption(label="فيتو ⛔", description="200 | الغي صوت ضدك في مافيا", value="veto", emoji="⛔"),
            discord.SelectOption(label="تأمين روليت 💉", description="300 | فرصة تانية لو اتضربت", value="roulette_ins", emoji="💉"),
            discord.SelectOption(label="تذكرة حظ 🎟️", description="120 | اكسب لحد 150 (+استخدم تذكرة)", value="luck_ticket", emoji="🎟️"),
            discord.SelectOption(label="صندوق حظ 🎁", description="250 | اكسب لحد 400 (+استخدم صندوق)", value="loot_box", emoji="🎁"),
        ]
        super().__init__(placeholder="اختار صنف من دكان الحفرة... 🛒", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        await interaction.response.send_modal(BuyForm(self.cog, v, self.prices[v], self.names[v]))

class ShopView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.add_item(ShopDropdown(cog))

class TransferModal(discord.ui.Modal, title="💸 Transfer Coins"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.target = discord.ui.TextInput(label="User ID or Mention", placeholder="e.g., 1234567890", required=True)
        self.amount = discord.ui.TextInput(label="Amount", placeholder="100", required=True)
        self.add_item(self.target)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try: amt = int(self.amount.value.strip())
        except:
            embed = discord.Embed(
                description="❌ The amount must be a number!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if amt <= 0:
            embed = discord.Embed(
                description="❌ The amount must be greater than zero!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        nums = re.findall(r'\d+', self.target.value)
        if not nums:
            embed = discord.Embed(
                description="❌ Invalid ID or mention!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        target_id = int(nums[0])
        
        if target_id == interaction.user.id:
            embed = discord.Embed(
                description="❌ You cannot transfer coins to yourself!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        bal = self.cog.get_balance(interaction.user.id)
        if bal < amt:
            embed = discord.Embed(
                description=f"❌ Insufficient balance! Your balance is {fmt(bal)} {COIN_EMOJI}.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        self.cog.update_balance(interaction.user.id, -amt)
        self.cog.update_balance(target_id, amt)
        embed = discord.Embed(
            description=f"✅ Successfully transferred **{fmt(amt)}** {COIN_EMOJI} to <@{target_id}>!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
        await self.cog.log_action("💸 Transfer", f"From {interaction.user.mention} to <@{target_id}>: {fmt(amt)}", discord.Color.orange())

class AdminActionModal(discord.ui.Modal):
    def __init__(self, cog, action):
        titles = {"add": "➕ Add Balance", "remove": "➖ Remove Balance", "reset": "⚠️ Reset Balance"}
        super().__init__(title=titles[action])
        self.cog = cog
        self.action = action
        self.target = discord.ui.TextInput(label="User ID", required=True)
        self.add_item(self.target)
        if action != "reset":
            self.amount = discord.ui.TextInput(label="Amount", required=True)
            self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        nums = re.findall(r'\d+', self.target.value)
        if not nums:
            embed = discord.Embed(
                description="❌ Invalid ID!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        target_id = int(nums[0])
        
        if self.action == "reset":
            bank.set_balance(target_id, 0)
            embed = discord.Embed(
                description=f"✅ Account reset for <@{target_id}>!",
                color=discord.Color.green()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        try: amt = int(self.amount.value)
        except:
            embed = discord.Embed(
                description="❌ Amount must be a number!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        if self.action == "add":
            self.cog.update_balance(target_id, amt)
            embed = discord.Embed(
                description=f"✅ Added {fmt(amt)} to <@{target_id}>",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif self.action == "remove":
            self.cog.update_balance(target_id, -amt)
            embed = discord.Embed(
                description=f"✅ Removed {fmt(amt)} from <@{target_id}>",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class AdminActionView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        
    @discord.ui.button(label="Add Coins", style=discord.ButtonStyle.success)
    async def add_btn(self, interaction, button): await interaction.response.send_modal(AdminActionModal(self.cog, "add"))
    @discord.ui.button(label="Remove Coins", style=discord.ButtonStyle.danger)
    async def rem_btn(self, interaction, button): await interaction.response.send_modal(AdminActionModal(self.cog, "remove"))
    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary)
    async def res_btn(self, interaction, button): await interaction.response.send_modal(AdminActionModal(self.cog, "reset"))

class CurrencyHubView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        
    @discord.ui.button(label="Daily", style=discord.ButtonStyle.success, emoji="🎁", custom_id="hub_daily")
    async def daily_btn(self, interaction, button):
        uid = str(interaction.user.id)
        now = bank.now()
        last, streak = bank.get_daily(uid)

        if now - last < 86400:
            next_available_time = int(last + 86400)
            embed = discord.Embed(
                description=f"❌ You already claimed it! Come back <t:{next_available_time}:R>",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if last and (now - last) > 86400 * 2:
            streak = 0
        bonus = min(streak, 7) * 10
        amt = random.randint(20, 45) + bonus
        self.cog.update_balance(interaction.user.id, amt)
        bank.set_daily(uid, now, streak + 1)
        embed = discord.Embed(
            description=f"✅ Claimed {fmt(amt)} {COIN_EMOJI} daily reward!\n🔥 ستريك **{streak + 1}** يوم (بونص +{fmt(min(streak + 1, 7) * 10)} بكرة)",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Top", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="hub_top")
    async def top_btn(self, interaction, button):
        srt = bank.top_balances(10)
        desc = "\n".join([f"**#{i+1}** <@{u}> ➔ **{fmt(b)}** {COIN_EMOJI}" for i, (u, b) in enumerate(srt)])
        embed = discord.Embed(
            title="🏆 Leaderboard",
            description=desc or "The vault is empty!",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    @discord.ui.button(label="Shop", style=discord.ButtonStyle.primary, emoji="🛒", custom_id="hub_shop")
    async def shop_btn(self, interaction, button):
        await interaction.response.send_message(view=ShopView(self.cog), ephemeral=True)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.primary, emoji="💸", custom_id="hub_transfer")
    async def transfer_btn(self, interaction, button):
        await interaction.response.send_modal(TransferModal(self.cog))
        
    @discord.ui.button(label="Admin Panel", style=discord.ButtonStyle.danger, emoji="⚙️", custom_id="hub_admin")
    async def admin_btn(self, interaction, button):
        if not self.cog.check_whitelist(interaction.user):
            embed = discord.Embed(
                description="❌ You don't have permission!",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_message("Admin Tools:", view=AdminActionView(self.cog), ephemeral=True)

# ==========================================
# 🏛️ Currency Cog
# ==========================================
class Currency(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    def check_whitelist(self, user: discord.Member) -> bool:
        if user.guild_permissions.administrator or user.id in OWNER_IDS: return True
        wl = bank.get_whitelist()
        if user.id in wl["users"]: return True
        user_role_ids = [role.id for role in user.roles]
        if any(role_id in wl["roles"] for role_id in user_role_ids): return True
        return False

    def update_balance(self, user_id: int, amount: int):
        return bank.update_balance(user_id, amount)

    def get_balance(self, user_id: int) -> int: return bank.get_balance(user_id)

    def add_item(self, user_id: int, item: str, amount: int = 1):
        return bank.add_item(user_id, item, amount)

    def remove_item(self, user_id: int, item: str, amount: int = 1) -> bool:
        return bank.remove_item(user_id, item, amount)

    def get_item_count(self, user_id: int, item: str) -> int:
        return bank.get_item_count(user_id, item)

    def get_inventory(self, user_id: int) -> dict:
        return bank.get_inventory(user_id)

    @commands.command(name='bankreset', aliases=['تصفير'])
    @commands.is_owner()
    async def bank_reset(self, ctx):
        """Fresh start: wipe ALL balances + inventory (keeps whitelist). Owner only."""
        bank.reset_all()
        try: os.replace(DATA_FILE, DATA_FILE + ".bak")
        except Exception: pass
        await ctx.send("🧹 تم تصفير البنك كله! كل الأرصدة والشنط بقت صفر. بداية نضيفة يا نجلاوي.")

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

    @commands.command(name='bal', aliases=['balance', 'رصيد', 'فلوسي', 'محفظتي'])
    async def balance(self, ctx):
        bal = self.get_balance(ctx.author.id)
        embed = discord.Embed(
            title="💳 Bank Account",
            description=f"Your current balance is:\n**{fmt(bal)}** {COIN_EMOJI}\n\nUse the buttons below to manage your account.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed, view=CurrencyHubView(self))

    @commands.command(name='inv', aliases=['inventory', 'شنطتي', 'حقيبتي'])
    async def inventory(self, ctx):
        inv = self.get_inventory(ctx.author.id)
        embed = discord.Embed(
            title=f"🎒 {ctx.author.display_name}'s Inventory",
            color=discord.Color.dark_purple()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        
        desc = ""
        ds = inv.get("double_shot", 0)
        sh = inv.get("shield", 0)
        
        if ds > 0: desc += f"🔫 **Double Shot:** {ds}\n"
        if sh > 0: desc += f"🛡️ **درع الحفره (Shield):** {sh}\n"
        _extra = {"mafia_scan": "🔍 **كشف مافيا:**", "veto": "⛔ **فيتو:**", "roulette_ins": "💉 **تأمين روليت:**", "luck_ticket": "🎟️ **تذكرة حظ:**", "loot_box": "🎁 **صندوق حظ:**"}
        for _k, _label in _extra.items():
            _c = inv.get(_k, 0)
            if _c > 0: desc += f"{_label} {_c}\n"
        
        embed.description = desc if desc else "Your inventory is empty!"
        await ctx.send(embed=embed)

    @commands.command(name='بروفايل', aliases=['profile', 'me', 'انا'])
    async def profile(self, ctx, target: discord.Member = None):
        """كارت البروفايل بتاعك: رصيد + ستريك + اغراض"""
        user = target or ctx.author
        bal = self.get_balance(user.id)
        _last, streak = bank.get_daily(str(user.id))
        inv = self.get_inventory(user.id)
        items_txt = " / ".join([f"{k}: {fmt(v)}" for k, v in inv.items() if v > 0]) or "فاضية يا معلم 🕸️"
        embed = discord.Embed(
            title=f"🪪 بروفايل {user.display_name}",
            description=f"💰 الرصيد: **{fmt(bal)}** {COIN_EMOJI}\n🔥 الستريك: **{streak}** يوم\n🎒 الشنطة: {items_txt}",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"كازينو الحفرة • {config.BRAND}")
        await ctx.send(embed=embed)

    @commands.command(name='استخدم', aliases=['use', 'افتح'])
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
        if self.get_item_count(ctx.author.id, key) <= 0:
            return await ctx.send(f"❌ معكش **{key}**! اشتريه من الشوب (`+رصيد` > Shop).")
        self.remove_item(ctx.author.id, key, 1)
        if key == "loot_box":
            reward = random.choice([30, 50, 80, 100, 150, 250, 400])
            self.update_balance(ctx.author.id, reward)
            desc = f"🎁 {ctx.author.mention} فتح صندوق الحظ وطلعله **{fmt(reward)}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{fmt(self.get_balance(ctx.author.id))}**"
        else:
            reward = random.choice([20, 40, 60, 100, 150])
            self.update_balance(ctx.author.id, reward)
            desc = f"🎟️ {ctx.author.mention} استخدم تذكرة الحظ وكسب **{fmt(reward)}** {COIN_EMOJI}!\n🏦 رصيدك بقى **{fmt(self.get_balance(ctx.author.id))}**"
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
    await bot.add_cog(Currency(bot))