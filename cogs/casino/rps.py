import asyncio
import logging
import re
import uuid
from functools import partial

import discord
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Separator, TextDisplay

from cogs.casino import bank
from cogs.casino.game_registry import (
    is_active_game,
    register_game_task,
    release_game_task,
)
from cogs.utility.theme import THEME, base_embed, embed_to_v2, get_currency

COIN = "<:ckrcoin:1513743032124575784>"
MOVE_NAMES = {"🪨": "حجر", "📄": "ورقة", "✂️": "مقص"}
BEATS = {"🪨": "✂️", "📄": "🪨", "✂️": "📄"}
MIN_BET = 10
MAX_BET = 5000
PAYOUT_RATE = 0.95


async def _settle_wager(game_id, winner_id=None):
    """Settle idempotently, retrying transient database failures before failing closed."""
    logger = logging.getLogger(__name__)
    for attempt in range(3):
        try:
            return bank.settle_rps_wager(game_id, winner_id)
        except Exception:
            logger.exception(
                "Failed to settle persisted RPS wager %s (attempt %s/3)",
                game_id,
                attempt + 1,
            )
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
    return {"settled": False, "reason": "database_error"}



_RECOVERY_DELAYS_SECONDS = (5, 15, 30, 60, 120, 300)
_RECOVERY_TASKS: dict[str, asyncio.Task] = {}


async def _retry_wager_recovery(game_id, winner_id=None, on_recovered=None):
    """Keep retrying unresolved escrow while this process is alive.

    The bank transition is idempotent. If the process shuts down, startup recovery
    refunds any remaining active escrow, so retrying cannot double-pay a wager.
    """
    game_id = str(game_id)
    logger = logging.getLogger(__name__)
    attempt = 0
    try:
        while True:
            delay = _RECOVERY_DELAYS_SECONDS[min(attempt, len(_RECOVERY_DELAYS_SECONDS) - 1)]
            await asyncio.sleep(delay)
            result = await _settle_wager(game_id, winner_id)
            if result.get("settled"):
                logger.info(
                    "Recovered pending RPS wager game_id=%s status=%s",
                    game_id,
                    result.get("status"),
                )
                if on_recovered is not None:
                    try:
                        await on_recovered(result)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Could not refresh the recovered RPS wager message")
                return
            if result.get("reason") == "not_found":
                logger.critical(
                    "Cannot recover RPS wager because its escrow record is missing game_id=%s",
                    game_id,
                )
                return

            attempt += 1
            if attempt % 3 == 0:
                logger.error(
                    "RPS wager remains escrowed after recovery attempts=%s game_id=%s reason=%s",
                    attempt,
                    game_id,
                    result.get("reason", "unknown"),
                )
    finally:
        current = asyncio.current_task()
        if _RECOVERY_TASKS.get(game_id) is current:
            _RECOVERY_TASKS.pop(game_id, None)


def _schedule_wager_recovery(game_id, winner_id=None, on_recovered=None):
    """Schedule at most one background recovery worker per wager."""
    key = str(game_id)
    existing = _RECOVERY_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_retry_wager_recovery(key, winner_id, on_recovered))
    _RECOVERY_TASKS[key] = task
    return task


# --- CHALLENGE VIEW ---
class RPSView(LayoutView):
    def __init__(self, ctx, player, opponent, bet, game_id):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.player = player
        self.opponent = opponent
        self.bet = bet
        self.game_id = str(game_id)
        self.choices = {}
        self.done = False
        self.message = None
        self._panel_text = TextDisplay(self._embed_content(self.make_embed()))
        self._buttons = []
        for emoji, label, callback_name in (
            ("🪨", "حجر", "rock"),
            ("📄", "ورقة", "paper"),
            ("✂️", "مقص", "scissors"),
        ):
            button = discord.ui.Button(
                emoji=emoji,
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"rps_{callback_name}",
            )
            button.callback = partial(getattr(self, callback_name), button=button)
            self._buttons.append(button)
        self.add_item(
            Container(
                self._panel_text,
                Separator(),
                ActionRow(*self._buttons),
                accent_color=THEME["teal"],
            )
        )

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


    @staticmethod
    def _embed_content(embed):
        parts = []
        if embed.title:
            parts.append(f"# {embed.title}")
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f"**{field.name}**\n{field.value}")
        if embed.footer and embed.footer.text:
            parts.append(f"-# {embed.footer.text}")
        return "\n\n".join(parts)[:4000]

    def _refresh_panel(self, embed=None):
        self._panel_text.content = self._embed_content(embed or self.make_embed())

    def _disable_buttons(self):
        for button in self._buttons:
            button.disabled = True

    def _queue_recovery_if_needed(self, result, winner_id=None):
        if result.get("settled"):
            return
        if result.get("reason") not in {"database_error", "balance_limit"}:
            return
        _schedule_wager_recovery(
            self.game_id,
            winner_id,
            self._publish_recovered_settlement,
        )

    async def _publish_recovered_settlement(self, result):
        if self.message is None:
            return
        if result.get("status") == "settled":
            winner_id = result.get("winner_id")
            if winner_id is None:
                description = "تم التأكد أن الرهان اتسوى بالفعل. لم نكرر أي خصم أو صرف."
            else:
                description = (
                    f"اكتملت تسوية الرهان بنجاح. الفائز: <@{winner_id}>.\n"
                    f"الجائزة المسجلة: **{int(result.get('payout', 0)):,}** {COIN}."
                )
        elif result.get("status") == "refunded":
            description = "اكتملت التسوية بإرجاع الرهان للطرفين بالكامل."
        else:
            description = "تمت معالجة الرهان بأمان."
        self._refresh_panel(
            base_embed(
                title="✅ تم تحديث حالة الرهان",
                description=description,
                color=THEME["teal"],
            )
        )
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            logging.getLogger(__name__).info(
                "Recovered RPS wager after its original message was deleted game_id=%s",
                self.game_id,
            )
        except discord.HTTPException:
            logging.getLogger(__name__).warning(
                "Recovered RPS wager but could not update its message game_id=%s",
                self.game_id,
                exc_info=True,
            )

    async def publish_result(self, embed, interaction=None):
        self._disable_buttons()
        self._refresh_panel(embed)

        try:
            if interaction is not None and not interaction.response.is_done():
                try:
                    await interaction.response.edit_message(view=self)
                except discord.NotFound:
                    await self.ctx.send(view=self)
                except discord.HTTPException:
                    logging.getLogger(__name__).exception(
                        "Could not update the RPS result through its interaction"
                    )
            elif self.message is not None:
                try:
                    await self.message.edit(view=self)
                except discord.NotFound:
                    await self.ctx.send(view=self)
                except discord.HTTPException:
                    logging.getLogger(__name__).exception(
                        "Could not edit the existing RPS result message; not sending a duplicate"
                    )
            else:
                await self.ctx.send(view=self)
        except discord.HTTPException:
            logging.getLogger(__name__).exception("Could not publish the RPS result")
        finally:
            self.stop()

    async def finish(self, interaction=None):
        if self.done:
            return
        self.done = True

        player_choice = self.choices.get(self.player.id)
        opponent_choice = self.choices.get(self.opponent.id)

        if player_choice is None or opponent_choice is None:
            result = await _settle_wager(self.game_id)
            if not result.get("settled"):
                self._queue_recovery_if_needed(result)
                description = "الرهان لسه محجوز بأمان. البوت هيعيد محاولة التسوية تلقائيًا، ولو حصل Restart هيتراجع الرهان عند بدء التشغيل."
            else:
                description = "حد اتأخر في الاختيار؛ رجعنا الرهان للطرفين بالكامل."
            embed = base_embed(
                title="⏰ التحدي انتهى",
                description=description,
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if player_choice == opponent_choice:
            result = await _settle_wager(self.game_id)
            if not result.get("settled"):
                self._queue_recovery_if_needed(result)
                description = "الرهان لسه محجوز بأمان. البوت هيعيد محاولة التسوية تلقائيًا، ولو حصل Restart هيتراجع الرهان عند بدء التشغيل."
            else:
                description = (
                    f"اختارتوا الاتنين **{MOVE_NAMES[player_choice]}**.\n"
                    "رجعنا الرهان كامل للطرفين."
                )
            embed = base_embed(
                title="🤝 تعادل!",
                description=description,
                color=THEME["grey"] if result.get("settled") else THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if BEATS[player_choice] == opponent_choice:
            winner, winning_move, losing_move = self.player, player_choice, opponent_choice
        else:
            winner, winning_move, losing_move = self.opponent, opponent_choice, player_choice

        result = await _settle_wager(self.game_id, winner.id)

        if not result.get("settled"):
            self._queue_recovery_if_needed(result, winner.id)
            embed = base_embed(
                title="⚠️ الرهان محفوظ",
                description=(
                    "حصل عطل أثناء تسوية الرهان. المبلغ ما زال مسجلًا في قاعدة البنك، "
                    "ومش هنكرر الخصم أو نصرف جائزة غير مؤكدة. راجعوا حالة البوت قبل بدء رهان جديد."
                ),
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        if result.get("status") == "refunded":
            embed = base_embed(
                title="⚠️ رجّعنا الرهان",
                description=(
                    "تعذر صرف الجائزة بأمان بسبب حد الرصيد؛ رجعنا رهان الطرفين بالكامل. "
                    "مفيش فائز مالي في الجولة دي."
                ),
                color=THEME["orange"],
            )
            return await self.publish_result(embed, interaction)

        prize = int(result["payout"])
        tax = int(result["tax"])
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
        settlement = await _settle_wager(self.game_id)

        if not settlement.get("settled"):
            self._queue_recovery_if_needed(settlement)
        description = (
            "تم إيقاف التحدي وإرجاع الرهان للطرفين."
            if settlement.get("settled")
            else "التحدي اتوقف؛ الرهان محفوظ، والبوت هيعيد محاولة التسوية تلقائيًا."
        )
        embed = base_embed(
            title="🛑 التحدي اتوقف",
            description=description,
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
        self._refresh_panel()
        if self.message is not None:
            await self.message.edit(view=self)

    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "🪨")

    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "📄")

    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "✂️")


# --- COG COMMANDS ---
class RPS(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._escrow_ready = False

    async def cog_load(self):
        logger = logging.getLogger(__name__)
        try:
            refunded = bank.refund_orphaned_rps_wagers()
            pending_wagers = bank.active_rps_wager_ids()
        except Exception:
            self._escrow_ready = False
            logger.exception(
                "RPS escrow recovery could not be verified; disabling new RPS wagers until restart/reload"
            )
            return

        self._escrow_ready = True
        if refunded:
            logger.warning(
                "Refunded %s interrupted RPS wager(s) during startup", refunded
            )
        for game_id in pending_wagers:
            _schedule_wager_recovery(game_id)
        if pending_wagers:
            logger.warning(
                "Scheduled background recovery for %s unresolved RPS wager(s)",
                len(pending_wagers),
            )

    @commands.hybrid_command(
        name="حومص",
        aliases=["rps", "حجرة", "مقص"],
        description="تحدَّ عضوًا في حجرة ورقة مقص برهان",
    )
    async def rps(self, ctx, bet: str | None = None, opponent: discord.Member | None = None):
        if not self._escrow_ready:
            return await ctx.send(
                view=embed_to_v2(
                    base_embed(
                        title="الرهان غير متاح مؤقتًا",
                        description=(
                            "سجل رهانات البنك لم يكتمل التحقق منه بعد. "
                            "مش هنبدأ رهان جديد لحد ما قاعدة البيانات تبقى آمنة."
                        ),
                        color=THEME["orange"],
                    )
                ),
                **({"ephemeral": True} if ctx.interaction is not None else {}),
            )

        if is_active_game(self.bot, ctx.channel.id):
            return await ctx.send(
                view=embed_to_v2(base_embed(
                    description="❌ فيه لعبة شغالة في الروم؛ استنوا لما تخلص.",
                    color=THEME["red_dark"],
                ))
            )

        raw_bet = (bet or "").strip().replace(",", "")
        amount = int(raw_bet) if re.fullmatch(r"[0-9]+", raw_bet) else 0

        usage = "/حومص" if ctx.interaction is not None else f"{ctx.prefix}حومص"
        if amount < MIN_BET:
            return await ctx.send(
                view=embed_to_v2(base_embed(
                    description=f"❌ مثال: `{usage} 100 @صاحبك` (أقل رهان {MIN_BET}).",
                    color=THEME["red_dark"],
                ))
            )
        if amount > MAX_BET:
            return await ctx.send(
                view=embed_to_v2(base_embed(
                    description=f"❌ أقصى رهان **{MAX_BET:,}**.",
                    color=THEME["red_dark"],
                ))
            )
        if opponent is None:
            return await ctx.send(view=embed_to_v2(base_embed(description="❌ منشن خصمك!", color=THEME["red_dark"])))
        if opponent.id == ctx.author.id:
            return await ctx.send(view=embed_to_v2(base_embed(description="❌ مش هتلاعب نفسك!", color=THEME["red_dark"])))
        if opponent.bot:
            return await ctx.send(view=embed_to_v2(base_embed(description="❌ البوتات مابتلعبش!", color=THEME["red_dark"])))

        currency = get_currency(self.bot)
        if currency is None:
            return await ctx.send(view=embed_to_v2(base_embed(description="❌ الفلوس واقعة! كلم نجلاوي.", color=THEME["red_dark"])))
        game_id = uuid.uuid4().hex
        if not bank.create_rps_wager(game_id, ctx.author.id, opponent.id, amount):
            player_balance = bank.get_balance(ctx.author.id)
            opponent_balance = bank.get_balance(opponent.id)
            if player_balance < amount:
                return await ctx.send(
                    view=embed_to_v2(base_embed(
                        description=f"❌ رصيدك **{player_balance:,}**، محتاج **{amount:,}**.",
                        color=THEME["red_dark"],
                    ))
                )
            if opponent_balance < amount:
                return await ctx.send(
                    view=embed_to_v2(base_embed(
                        description=f"❌ {opponent.mention} رصيده مش كفاية للرهان.",
                        color=THEME["red_dark"],
                    ))
                )
            return await ctx.send(
                view=embed_to_v2(base_embed(
                    description="❌ تعذر تسجيل الرهان بأمان. مفيش مبلغ اتخصم؛ راجع الرصيد وحاول تاني.",
                    color=THEME["red_dark"],
                ))
            )

        view = RPSView(ctx, ctx.author, opponent, amount, game_id)
        task = asyncio.current_task()
        if task is None or not register_game_task(
            self.bot,
            ctx.channel.id,
            task,
            ctx.author.id,
            "Rock Paper Scissors",
            view=view,
        ):
            refund = await _settle_wager(game_id)
            if not refund.get("settled") and refund.get("reason") in {"database_error", "balance_limit"}:
                _schedule_wager_recovery(game_id)
            message = (
                "❌ فيه لعبة شغالة في الروم؛ رجعنا الرهان للطرفين."
                if refund.get("settled")
                else "❌ فيه لعبة شغالة في الروم. الرهان محفوظ، والبوت هيعيد محاولة التسوية تلقائيًا."
            )
            return await ctx.send(
                view=embed_to_v2(base_embed(
                    description=message,
                    color=THEME["red_dark"],
                ))
            )

        try:
            view.message = await ctx.send(view=view)
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
