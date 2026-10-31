"""Currency transfer modal and member validation."""

import asyncio
import logging
import re

import discord

from cogs.casino import bank
from cogs.casino.currency_catalog import fmt, get_coin_emoji
from cogs.utility.theme import StatusPanel

logger = logging.getLogger(__name__)


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

        coin_emoji = get_coin_emoji(getattr(interaction, 'guild', None))
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

        # Acknowledge before member lookup.
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
                    f"رصيدك الحالي **{fmt(balance_after)} {coin_emoji}** ولا يكفي لتحويل **{fmt(amount)} {coin_emoji}**.",
                    "warning",
                ),
                ephemeral=True,
            )

        await interaction.followup.send(
            view=StatusPanel(
                "تم التحويل بنجاح",
                f"حوّلت **{fmt(amount)} {coin_emoji}** إلى {recipient.mention}.\nرصيدك الجديد: **{fmt(balance_after)} {coin_emoji}**.",
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
