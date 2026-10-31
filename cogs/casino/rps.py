import asyncio
import logging
import uuid
from functools import partial

import discord
from discord.ext import commands

from cogs.casino import bank
from cogs.casino.bet_validation import parse_bet_amount
from cogs.casino.game_registry import (
    is_active_game,
    register_game_task,
    release_game_task,
)
from cogs.casino.rps_core import (
    BEATS,
    COIN,
    MAX_BET,
    MIN_BET,
    MOVE_NAMES,
    PAYOUT_RATE,
)
from cogs.casino.rps_views import RPSView
from cogs.utility.theme import (
    THEME,
    StatusPanel,
    base_embed,
    embed_to_v2,
    get_currency,
    send_status,
)

async def _settle_wager(game_id, winner_id=None):
    """Settle idempotently, retrying transient database failures before failing closed."""
    logger = logging.getLogger(__name__)
    for attempt in range(3):
        try:
            return await asyncio.to_thread(bank.settle_rps_wager, game_id, winner_id)
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
_STARTUP_PANEL_TASKS: set[asyncio.Task] = set()


async def _publish_interrupted_rps_panel(bot, record, receipt):
    """Close a stale challenge panel only after both stakes are safely refunded."""
    if receipt.get("status") != "refunded":
        return
    if receipt.get("outcome") != "refund":
        return
    channel_id = record.get("channel_id")
    message_id = record.get("message_id")
    if not channel_id or not message_id:
        return

    amount = int(record.get("amount", 0))
    total_refund = amount * 2
    try:
        await bot.wait_until_ready()
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            view=StatusPanel(
                "التحدي اتوقف بعد إعادة التشغيل",
                (
                    "التحدي السابق ماكملش قبل ما البوت يقفل. "
                    f"اترد **{amount:,} {COIN} لكل لاعب** "
                    f"(إجمالي **{total_refund:,} {COIN}** للطرفين). "
                    "الأزرار القديمة اتقفلت؛ ابدأوا تحديًا جديدًا لو حابين."
                ),
                "warning",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logging.getLogger(__name__).info(
            "Interrupted RPS wager refunded but original panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logging.getLogger(__name__).warning(
            "Interrupted RPS wager refunded but the bot cannot access its panel game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logging.getLogger(__name__).warning(
            "Interrupted RPS wager refunded but its old panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Interrupted RPS wager was refunded, but unexpected panel cleanup failed game_id=%s",
            record.get("game_id"),
        )


async def _publish_expired_rps_challenge_panel(bot, record):
    """Close a pre-acceptance invite after restart; no stake was ever reserved."""
    channel_id = record.get("channel_id")
    message_id = record.get("message_id")
    if not channel_id or not message_id:
        return

    try:
        await bot.wait_until_ready()
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            view=StatusPanel(
                "انتهى التحدي بعد إعادة التشغيل",
                "التحدي ما اتقبلش قبل ما البوت يعيد التشغيل، فتم إقفاله من غير حجز أو خصم أي عملات. ابدأوا تحديًا جديدًا لو حابين.",
                "warning",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except asyncio.CancelledError:
        raise
    except discord.NotFound:
        logging.getLogger(__name__).info(
            "Expired unaccepted RPS challenge panel no longer exists game_id=%s",
            record.get("game_id"),
        )
    except discord.Forbidden:
        logging.getLogger(__name__).warning(
            "Expired unaccepted RPS challenge could not be closed due to channel permissions game_id=%s",
            record.get("game_id"),
        )
    except discord.HTTPException:
        logging.getLogger(__name__).warning(
            "Expired unaccepted RPS challenge but its panel could not be updated game_id=%s",
            record.get("game_id"),
            exc_info=True,
        )


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


class RPS(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._escrow_ready = False

    async def cog_load(self):
        logger = logging.getLogger(__name__)
        try:
            # Capture panel references before the initial refund mutates escrow state.
            expired_challenges = await asyncio.to_thread(bank.expire_pending_rps_challenges)
            records = await asyncio.to_thread(bank.active_rps_wager_records)
            refunded = await asyncio.to_thread(bank.refund_orphaned_rps_wagers)
            pending_wagers = await asyncio.to_thread(bank.active_rps_wager_ids)
            active_ids = set(pending_wagers)
            records_by_id = {str(record["game_id"]): record for record in records}

            for challenge in expired_challenges:
                if challenge.get("channel_id") and challenge.get("message_id"):
                    task = asyncio.create_task(
                        _publish_expired_rps_challenge_panel(self.bot, challenge)
                    )
                    _STARTUP_PANEL_TASKS.add(task)
                    task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

            for game_id, record in records_by_id.items():
                has_panel = bool(record.get("channel_id") and record.get("message_id"))
                if game_id in active_ids:
                    callback = (
                        partial(_publish_interrupted_rps_panel, self.bot, record)
                        if has_panel
                        else None
                    )
                    _schedule_wager_recovery(game_id, on_recovered=callback)
                    continue

                receipt = await asyncio.to_thread(bank.rps_wager_status, game_id)
                if (
                    has_panel
                    and receipt is not None
                    and receipt.get("status") == "refunded"
                    and receipt.get("outcome") == "refund"
                ):
                    task = asyncio.create_task(
                        _publish_interrupted_rps_panel(self.bot, record, receipt)
                    )
                    _STARTUP_PANEL_TASKS.add(task)
                    task.add_done_callback(_STARTUP_PANEL_TASKS.discard)

            # Defensive fallback if an older/partial schema yields active IDs without rows.
            for game_id in pending_wagers:
                if str(game_id) not in records_by_id:
                    _schedule_wager_recovery(game_id)
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
        if pending_wagers:
            logger.warning(
                "Scheduled background recovery for %s unresolved RPS wager(s)",
                len(pending_wagers),
            )

    @commands.hybrid_command(
        name="حومص",
        aliases=["rps", "rock-paper-scissors", "rockpaperscissors", "حجرة", "مقص"],
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
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة شغالة في الروم؛ استنوا لما تخلص قبل ما تبدأ تحديًا جديدًا.",
                "warning",
            )

        amount = parse_bet_amount(bet, max_bet=MAX_BET)

        usage = "/حومص" if ctx.interaction is not None else f"{ctx.prefix}حومص"
        if amount < MIN_BET:
            return await send_status(
                ctx,
                "قيمة الرهان مش مناسبة",
                f"مثال: `{usage} 100 @صاحبك`. أقل رهان **{MIN_BET:,}** {COIN}.",
                "warning",
            )
        if amount > MAX_BET:
            return await send_status(
                ctx,
                "قيمة الرهان أعلى من الحد",
                f"أقصى رهان مسموح هو **{MAX_BET:,} {COIN}**.",
                "warning",
            )
        if opponent is None:
            return await send_status(ctx, "حدد خصمك", "منشن عضوًا من السيرفر لبدء التحدي.", "warning")
        if opponent.id == ctx.author.id:
            return await send_status(ctx, "اختار لاعبًا آخر", "مينفعش تتحدى نفسك في حجرة ورقة مقص.", "warning")
        if opponent.bot:
            return await send_status(ctx, "الخصم غير صالح", "حسابات البوتات مش متاحة للتحديات.", "warning")

        currency = get_currency(self.bot)
        if currency is None:
            return await send_status(
                ctx,
                "الاقتصاد غير متاح",
                "نظام العملات مش متاح حاليًا؛ التحدي اتوقف قبل حجز أي رصيد.",
                "warning",
            )

        try:
            player_balance, opponent_balance = await asyncio.gather(
                asyncio.to_thread(bank.get_balance, ctx.author.id),
                asyncio.to_thread(bank.get_balance, opponent.id),
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not verify RPS challenge balances challenger_id=%s opponent_id=%s",
                ctx.author.id,
                opponent.id,
            )
            return await send_status(
                ctx,
                "تعذر التحقق من الرصيد",
                "حصل عطل أثناء قراءة الأرصدة. مفيش رهان اتعمل؛ جرّب تاني بعد شوية.",
                "warning",
            )

        if player_balance < amount:
            return await send_status(
                ctx,
                "رصيدك مش كفاية",
                f"رصيدك الحالي **{player_balance:,} {COIN}**، ومحتاج **{amount:,} {COIN}** عشان تبعت التحدي.",
                "warning",
            )
        if opponent_balance < amount:
            return await send_status(
                ctx,
                "رصيد الخصم مش كفاية",
                f"{opponent.mention} رصيده الحالي **{opponent_balance:,} {COIN}**، وأقل رهان مطلوب **{amount:,} {COIN}**. مفيش رصيد اتحجز.",
                "warning",
            )

        game_id = uuid.uuid4().hex
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
            view.stop()
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة شغالة في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ تحديًا جديدًا.",
                "warning",
            )

        try:
            try:
                created = await asyncio.to_thread(
                    bank.create_rps_challenge,
                    game_id,
                    ctx.author.id,
                    opponent.id,
                    amount,
                    channel_id=int(ctx.channel.id),
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not create RPS challenge invitation game_id=%s",
                    game_id,
                )
                view.stop()
                await send_status(
                    ctx,
                    "تعذر إنشاء التحدي",
                    "قاعدة بيانات الرهانات لم تؤكد إنشاء الدعوة. مفيش رصيد اتحجز؛ جرّب تاني.",
                    "warning",
                )
                return

            if not created:
                view.stop()
                await send_status(
                    ctx,
                    "تعذر إنشاء التحدي",
                    "لم نقدر نحفظ الدعوة بأمان. مفيش رصيد اتحجز؛ جرّب تاني.",
                    "warning",
                )
                return

            try:
                view.message = await ctx.send(
                    view=view,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        users=[opponent],
                        roles=False,
                        replied_user=False,
                    ),
                )
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    bank.cancel_rps_challenge,
                    game_id,
                    ctx.author.id,
                )
                raise
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not publish RPS challenge invitation game_id=%s requester_id=%s",
                    game_id,
                    ctx.author.id,
                )
                try:
                    await asyncio.to_thread(
                        bank.cancel_rps_challenge,
                        game_id,
                        ctx.author.id,
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not cancel unpublished RPS invitation game_id=%s",
                        game_id,
                    )
                await send_status(
                    ctx,
                    "تعذر إرسال التحدي",
                    "حصل عطل أثناء إرسال الدعوة. الدعوة اتلغت أو هتنتهي بأمان من غير حجز أي عملات.",
                    "warning",
                )
                return

            try:
                stored_reference = await asyncio.to_thread(
                    bank.set_rps_challenge_message_reference,
                    game_id,
                    int(ctx.channel.id),
                    int(view.message.id),
                )
                if not stored_reference:
                    logging.getLogger(__name__).error(
                        "RPS invitation exists but its panel reference was not saved game_id=%s",
                        game_id,
                    )
            except Exception:
                # This reference is only for cleanup; acceptance/escrow remains guarded by DB state.
                logging.getLogger(__name__).exception(
                    "Could not persist pending RPS invitation panel reference game_id=%s",
                    game_id,
                )
            view.schedule_deadline(60.0)
            await view.wait()
        except asyncio.CancelledError:
            await view.abort()
            raise
        except Exception:
            logging.getLogger(__name__).exception(
                "Unexpected RPS command lifecycle error game_id=%s requester_id=%s",
                game_id,
                ctx.author.id,
            )
            if not view.done:
                await view.abort()
            raise
        finally:
            release_game_task(self.bot, ctx.channel.id, task)


async def setup(bot):
    await bot.add_cog(RPS(bot))


__all__ = [
    "BEATS",
    "COIN",
    "MAX_BET",
    "MIN_BET",
    "MOVE_NAMES",
    "PAYOUT_RATE",
]
