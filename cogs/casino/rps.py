import asyncio
import re

import discord
from discord.ext import commands

from cogs.casino.game_registry import is_active_game, register_game_task, release_game_task
from cogs.utility.theme import THEME, WatermarkView, base_embed, get_currency

COIN = "<:ckrcoin:1513743032124575784>"
MOVE_NAMES = {"🪨": "حجر", "📄": "ورقة", "✂️": "مقص"}
BEATS = {"🪨": "✂️", "📄": "🪨", "✂️": "📄"}
MIN_BET = 10
MAX_BET = 5000
PAYOUT_RATE = 0.95


# --- CHALLENGE VIEW ---
class RPSView(discord.ui.View):
    def __init__(self, ctx, player, opponent, bet):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.player = player
        self.opponent = opponent
        self.bet = bet
        self.choices = {}
        self.done = False
        self.message = None

    def make_embed(self):
        player_status = "✅" if self.player.id in self.choices else "⏳"
        opponent_status = "✅" if self.opponent.id in self.choices else "⏳"
        prize = int(self.bet * 2 * PAYOUT_RATE)
        return base_embed(
            title="✊ حومص!",
            description=(
                f"{self.player.mention} **VS** {self.opponent.mention}\n"
                f"💰 الرهان: **{self.bet:,}** لكل لاعب\n"
                f"🏆 جائزة الفائز: **{prize:,}** {COIN}\n\n"
                f"{self.player.mention}: {player_status}\n"
                f"{self.opponent.mention}: {opponent_status}\n\n"
                "اختار حركتك بسرية، والنتيجة هتظهر لما الاتنين يختاروا."
            ),
            color=THEME["teal"],
        )

    async def publish_result(self, embed, interaction=None):
        for item in self.children:
            item.disabled = True

        try:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            elif self.message is not None:
                await self.message.edit(embed=embed, view=self)
            else:
                await self.ctx.send(embed=embed, view=self)
        except discord.HTTPException:
            await self.ctx.send(embed=embed, view=WatermarkView())
        finally:
            self.stop()

    async def finish(self, interaction=None):
        if self.done:
            return
        self.done = True

        currency = get_currency(self.ctx.bot)
        prize = int(self.bet * 2 * PAYOUT_RATE)
        tax = self.bet * 2 - prize
        player_choice = self.choices.get(self.player.id)
        opponent_choice = self.choices.get(self.opponent.id)

        if player_choice is None or opponent_choice is None:
            if currency:
                currency.update_balance(self.player.id, self.bet)
                currency.update_balance(self.opponent.id, self.bet)
            embed = base_embed(
                title="⏰ التحدي انتهى",
                description="حد اتأخر في الاختيار؛ رجعنا الرهان للطرفين.",
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if player_choice == opponent_choice:
            if currency:
                currency.update_balance(self.player.id, self.bet)
                currency.update_balance(self.opponent.id, self.bet)
            embed = base_embed(
                title="🤝 تعادل!",
                description=(
                    f"اختارتوا الاتنين **{MOVE_NAMES[player_choice]}**.\n"
                    "رجعنا الرهان كامل للطرفين."
                ),
                color=THEME["grey"],
            )
            return await self.publish_result(embed, interaction)

        if BEATS[player_choice] == opponent_choice:
            winner, winning_move, losing_move = self.player, player_choice, opponent_choice
        else:
            winner, winning_move, losing_move = self.opponent, opponent_choice, player_choice

        if currency:
            try:
                currency.update_balance(winner.id, prize)
            except OverflowError:
                currency.update_balance(self.player.id, self.bet)
                currency.update_balance(self.opponent.id, self.bet)
                embed = base_embed(
                    title="⚠️ تعذر صرف الجائزة",
                    description="رصيد الفائز وصل للحد الأقصى؛ رجعنا الرهان للطرفين.",
                    color=THEME["orange"],
                )
                return await self.publish_result(embed, interaction)

        embed = base_embed(
            title="🏆 فايز!",
            description=(
                f"**{MOVE_NAMES[winning_move]}** كسبت **{MOVE_NAMES[losing_move]}**.\n\n"
                f"🎉 الفائز: {winner.mention}\n"
                f"💰 الجائزة: **{prize:,}** {COIN}\n"
                f"🧾 ضريبة البنك: **{tax:,}** {COIN}"
            ),
            color=THEME["gold"],
        )
        embed.set_thumbnail(url=winner.display_avatar.url)
        await self.publish_result(embed, interaction)

    async def on_timeout(self):
        await self.finish()

    async def abort(self):
        if self.done:
            return
        self.done = True
        currency = get_currency(self.ctx.bot)
        if currency:
            currency.update_balance(self.player.id, self.bet)
            currency.update_balance(self.opponent.id, self.bet)
        embed = base_embed(
            title="🛑 التحدي اتوقف",
            description="تم إيقاف التحدي؛ رجعنا الرهان للطرفين.",
            color=THEME["orange"],
        )
        await self.publish_result(embed)

    async def choose(self, interaction: discord.Interaction, move: str):
        if self.done:
            return await interaction.response.send_message("التحدي خلص بالفعل.", ephemeral=True)
        if interaction.user.id not in (self.player.id, self.opponent.id):
            return await interaction.response.send_message("مش طرف في التحدي.", ephemeral=True)
        if interaction.user.id in self.choices:
            return await interaction.response.send_message("اختارت خلاص!", ephemeral=True)

        self.choices[interaction.user.id] = move
        if len(self.choices) == 2:
            return await self.finish(interaction)

        await interaction.response.send_message("✅ سجلنا اختيارك بسرية.", ephemeral=True)
        if self.message is not None:
            await self.message.edit(embed=self.make_embed(), view=self)

    @discord.ui.button(emoji="🪨", label="حجر", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "🪨")

    @discord.ui.button(emoji="📄", label="ورقة", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "📄")

    @discord.ui.button(emoji="✂️", label="مقص", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "✂️")


# --- COG COMMANDS ---
class RPS(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="حومص",
        aliases=["rps", "حجرة", "مقص"],
        description="تحدَّ عضوًا في حجرة ورقة مقص برهان",
    )
    async def rps(self, ctx, bet: str = None, opponent: discord.Member = None):
        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send(
                embed=base_embed(
                    description="❌ فيه لعبة شغالة في الروم؛ استنوا لما تخلص.",
                    color=THEME["red_dark"],
                )
            )

        try:
            amount = int(re.sub(r"\\D", "", bet or ""))
        except ValueError:
            amount = 0

        usage = "/حومص" if ctx.interaction is not None else f"{ctx.prefix}حومص"
        if amount < MIN_BET:
            return await ctx.send(
                embed=base_embed(
                    description=f"❌ مثال: `{usage} 100 @صاحبك` (أقل رهان {MIN_BET}).",
                    color=THEME["red_dark"],
                )
            )
        if amount > MAX_BET:
            return await ctx.send(
                embed=base_embed(
                    description=f"❌ أقصى رهان **{MAX_BET:,}**.",
                    color=THEME["red_dark"],
                )
            )
        if opponent is None:
            return await ctx.send(embed=base_embed(description="❌ منشن خصمك!", color=THEME["red_dark"]))
        if opponent.id == ctx.author.id:
            return await ctx.send(embed=base_embed(description="❌ مش هتلاعب نفسك!", color=THEME["red_dark"]))
        if opponent.bot:
            return await ctx.send(embed=base_embed(description="❌ البوتات مابتلعبش!", color=THEME["red_dark"]))

        currency = get_currency(self.bot)
        if currency is None:
            return await ctx.send(embed=base_embed(description="❌ الفلوس واقعة! كلم نجلاوي.", color=THEME["red_dark"]))
        if not currency.place_bet(ctx.author.id, opponent.id, amount):
            player_balance = currency.get_balance(ctx.author.id)
            opponent_balance = currency.get_balance(opponent.id)
            if player_balance < amount:
                return await ctx.send(
                    embed=base_embed(
                        description=f"❌ رصيدك **{player_balance:,}**، محتاج **{amount:,}**.",
                        color=THEME["red_dark"],
                    )
                )
            if opponent_balance < amount:
                return await ctx.send(
                    embed=base_embed(
                        description=f"❌ {opponent.mention} رصيده مش كفاية للرهان.",
                        color=THEME["red_dark"],
                    )
                )
            return await ctx.send(
                embed=base_embed(
                    description="❌ الرصيد اتغير أثناء تسجيل الرهان. راجع الرصيد وحاول تاني.",
                    color=THEME["red_dark"],
                )
            )

        view = RPSView(ctx, ctx.author, opponent, amount)
        task = asyncio.current_task()
        if task is None or not register_game_task(
            self.bot,
            ctx.channel.id,
            task,
            ctx.author.id,
            "Rock Paper Scissors",
            view=view,
        ):
            currency.update_balance(ctx.author.id, amount)
            currency.update_balance(opponent.id, amount)
            return await ctx.send(
                embed=base_embed(
                    description="❌ فيه لعبة شغالة في الروم؛ رجعنا الرهان للطرفين.",
                    color=THEME["red_dark"],
                )
            )

        try:
            view.message = await ctx.send(
                content=f"{opponent.mention} ⚔️ {ctx.author.mention} بيتحداك على **{amount:,}** {COIN}!",
                embed=view.make_embed(),
                view=view,
            )
            await view.wait()
        except asyncio.CancelledError:
            await view.abort()
            raise
        except Exception:
            if not view.done:
                await view.abort()
            raise
        finally:
            release_game_task(self.bot, ctx.channel.id, task)


async def setup(bot):
    await bot.add_cog(RPS(bot))
