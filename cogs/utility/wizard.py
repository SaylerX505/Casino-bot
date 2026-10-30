"""Discord setup command and public wizard view exports."""

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utility.wizard_common import WizardLayout, _can_manage_wizard
from cogs.utility.wizard_views import (
    AdminRoleWizardView,
    AISettingsWizardView,
    GameChannelWizardView,
    LogChannelWizardView,
    StaffRoleWizardView,
    WizardHomeView,
    WizardReviewView,
)


class SetupWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="wizard",
        description="إعداد رولات الستاف وقناة عامة لكل الألعاب خطوة بخطوة",
    )
    @app_commands.guild_only()
    async def wizard(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not _can_manage_wizard(interaction):
            return await interaction.response.send_message(
                "فتح المعالج متاح لمالك السيرفر أو Administrator أو رولات إدارة البوت التي تم إعدادها مسبقًا.",
                ephemeral=True,
            )
        view = WizardHomeView(guild, interaction.user)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(SetupWizard(bot))

__all__ = [
    "AISettingsWizardView",
    "AdminRoleWizardView",
    "GameChannelWizardView",
    "LogChannelWizardView",
    "SetupWizard",
    "StaffRoleWizardView",
    "WizardHomeView",
    "WizardLayout",
    "WizardReviewView",
    "setup",
]
