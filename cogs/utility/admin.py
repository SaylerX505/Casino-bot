import discord
import os
import time
from discord import app_commands
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

from cogs.casino import bank


class AdminResult(LayoutView):
    def __init__(self, title: str, message: str, success: bool = True):
        super().__init__(timeout=120)
        color = 0x2AA6A4 if success else 0x8B1E1E
        self.add_item(
            Container(
                TextDisplay(f"# {title}\n{message}"),
                Separator(),
                accent_color=color,
            )
        )


class BankResetConfirmPanel(LayoutView):
    def __init__(self, requester_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.guild_id = guild_id
        container = Container(
            TextDisplay(
                "# ⚠️ تصفير الاقتصاد\\n"
                "العملية هتمسح أرصدة الأعضاء وأغراضهم. هنحفظ نسخة احتياطية قبل التصفير."
            ),
            Separator(),
            accent_color=0x8B1E1E,
        )
        row = ActionRow()
        confirm = discord.ui.Button(
            label="تأكيد التصفير",
            style=discord.ButtonStyle.danger,
            custom_id="admin_bank_reset_confirm",
        )
        confirm.callback = self.confirm_reset
        cancel = discord.ui.Button(
            label="إلغاء",
            style=discord.ButtonStyle.secondary,
            custom_id="admin_bank_reset_cancel",
        )
        cancel.callback = self.cancel_reset
        row.add_item(confirm)
        row.add_item(cancel)
        container.add_item(row)
        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                view=AdminResult("تأكيد خاص", "صاحب الطلب فقط يقدر يؤكد العملية.", False),
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self.guild_id or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                view=AdminResult("صلاحية غير كافية", "لازم تكون Administrator في نفس السيرفر.", False),
                ephemeral=True,
            )
            return False
        return True

    async def confirm_reset(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            os.makedirs(os.path.join("data", "backups"), exist_ok=True)
            backup_path = os.path.join(
                "data",
                "backups",
                f"bank-reset-{self.guild_id}-{time.time_ns()}.db",
            )
            bank.backup_database(backup_path)
            bank.reset_all()
        except Exception:
            await interaction.edit_original_response(
                view=AdminResult("فشل التصفير", "ماقدرناش نكمل العملية. راجع سجل الأخطاء.", False),
            )
            raise

        await interaction.edit_original_response(
            view=AdminResult("تم تصفير الاقتصاد", f"اتحفظت نسخة احتياطية قبل التصفير: `{os.path.basename(backup_path)}`."),
        )
        self.stop()

    async def cancel_reset(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=AdminResult("تم الإلغاء", "مفيش أي بيانات اتغيرت."),
        )
        self.stop()

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True

class AdminCommands(commands.Cog):
    admin = app_commands.Group(
        name="admin",
        description="إدارة إعدادات البوت داخل السيرفر",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot):
        self.bot = bot

    async def _require_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is not None and interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            view=AdminResult(
                "غير مسموح",
                "أوامر الإدارة متاحة فقط لحسابات Discord التي تمتلك صلاحية Administrator.",
                success=False,
            ),
            ephemeral=True,
        )
        return False

    @admin.command(name="access-add", description="السماح لعضو باستخدام أدوات إدارة البوت المحددة")
    @app_commands.describe(member="العضو المراد إضافته لقائمة أدمنز البوت")
    async def access_add(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._require_admin(interaction):
            return
        if member.bot:
            return await interaction.response.send_message(
                view=AdminResult("عضو غير صالح", "اختار عضوًا حقيقيًا، مش بوت.", False),
                ephemeral=True,
            )
        added = bank.add_guild_admin(interaction.guild_id, member.id)
        message = (
            f"تمت إضافة {member.mention} لقائمة أدمنز البوت في السيرفر ده."
            if added
            else f"{member.mention} موجود بالفعل في قائمة أدمنز البوت."
        )
        await interaction.response.send_message(
            view=AdminResult("صلاحيات البوت", message),
            ephemeral=True,
        )

    @admin.command(name="access-remove", description="إزالة عضو من قائمة أدمنز البوت")
    @app_commands.describe(member="العضو المراد إزالة وصوله")
    async def access_remove(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._require_admin(interaction):
            return
        removed = bank.remove_guild_admin(interaction.guild_id, member.id)
        message = (
            f"تمت إزالة {member.mention} من قائمة أدمنز البوت."
            if removed
            else f"{member.mention} مش موجود في القائمة."
        )
        await interaction.response.send_message(
            view=AdminResult("صلاحيات البوت", message),
            ephemeral=True,
        )

    @admin.command(name="access-list", description="عرض أدمنز البوت المحددين لهذا السيرفر")
    async def access_list(self, interaction: discord.Interaction):
        if not await self._require_admin(interaction):
            return
        user_ids = bank.list_guild_admins(interaction.guild_id)
        if not user_ids:
            description = "لسه مفيش أدمنز إضافيين. استخدم /admin access-add لإضافة عضو."
        else:
            description = "\n".join(f"• <@{user_id}>" for user_id in user_ids[:25])
            if len(user_ids) > 25:
                description += f"\nوباقي {len(user_ids) - 25} عضو."
        await interaction.response.send_message(
            view=AdminResult("أدمنز البوت", description),
            ephemeral=True,
        )

    @admin.command(name="economy-credit", description="إضافة عملات لعضو")
    @app_commands.describe(member="العضو المستهدف", amount="المبلغ المطلوب إضافته")
    async def economy_credit(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 1_000_000_000],
    ):
        if not await self._require_admin(interaction):
            return
        credited, balance = bank.credit_balance(member.id, amount)
        if not credited:
            return await interaction.response.send_message(
                view=AdminResult("تعذر إضافة العملات", "المبلغ يتجاوز الحد المدعوم للرصيد.", False),
                ephemeral=True,
            )
        await interaction.response.send_message(
            view=AdminResult(
                "تمت إضافة العملات",
                f"تمت إضافة **{amount:,}** إلى {member.mention}.\nرصيده الجديد: **{balance:,}**.",
            ),
            ephemeral=True,
        )

    @admin.command(name="economy-debit", description="خصم عملات من عضو")
    @app_commands.describe(member="العضو المستهدف", amount="المبلغ المطلوب خصمه")
    async def economy_debit(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 1_000_000_000],
    ):
        if not await self._require_admin(interaction):
            return
        removed, balance = bank.remove_balance(member.id, amount)
        if not removed:
            return await interaction.response.send_message(
                view=AdminResult(
                    "تعذر خصم العملات",
                    f"رصيد {member.mention} الحالي **{balance:,}** ولا يكفي للخصم المطلوب.",
                    False,
                ),
                ephemeral=True,
            )
        await interaction.response.send_message(
            view=AdminResult(
                "تم خصم العملات",
                f"تم خصم **{amount:,}** من {member.mention}.\nرصيده الجديد: **{balance:,}**.",
            ),
            ephemeral=True,
        )

    @admin.command(name="ai-toggle", description="تشغيل أو إيقاف محسن")
    @app_commands.describe(enabled="فعّل محسن أو أوقفه")
    async def ai_toggle(self, interaction: discord.Interaction, enabled: bool):
        if not await self._require_admin(interaction):
            return
        cog = self.bot.get_cog("AI")
        if cog is None:
            return await interaction.response.send_message(
                view=AdminResult("محسن غير متاح", "إضافة الذكاء الاصطناعي مش محملة حاليًا.", False),
                ephemeral=True,
            )
        from cogs.utility.ai import save_ai_data

        cog.data["ai_enabled"] = enabled
        save_ai_data(cog.data)
        state = "شغال" if enabled else "متوقف"
        await interaction.response.send_message(
            view=AdminResult("إعدادات محسن", f"محسن دلوقتي **{state}**."),
            ephemeral=True,
        )

    @admin.command(name="bank-reset", description="تصفير الاقتصاد بعد تأكيد ونسخة احتياطية")
    async def bank_reset(self, interaction: discord.Interaction):
        if not await self._require_admin(interaction):
            return
        await interaction.response.send_message(
            view=BankResetConfirmPanel(interaction.user.id, interaction.guild_id),
            ephemeral=True,
        )
    @admin.command(name="status", description="عرض حالة البوت وأدمنز البوت")
    async def admin_status(self, interaction: discord.Interaction):
        if not await self._require_admin(interaction):
            return
        admins = bank.list_guild_admins(interaction.guild_id)
        ai_cog = self.bot.get_cog("AI")
        ai_state = "مش محمل" if ai_cog is None else ("شغال" if ai_cog.data.get("ai_enabled", True) else "متوقف")
        active_games = len(getattr(self.bot, "active_games", {}))
        description = (
            f"**أدمنز البوت:** {len(admins)}\n"
            f"**محسن:** {ai_state}\n"
            f"**الألعاب النشطة:** {active_games}"
        )
        await interaction.response.send_message(
            view=AdminResult("حالة البوت", description),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
