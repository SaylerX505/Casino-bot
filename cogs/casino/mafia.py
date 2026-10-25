import discord
from discord.ui import ActionRow, Container, LayoutView, MediaGallery, Separator, TextDisplay
from discord.ext import commands
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task, set_game_view
import asyncio
import logging
import random
from functools import partial
import config

from cogs.casino.game_assets import SERVER_LOGO_URL
from cogs.casino.mafia_art import (
    generate_action_image,
    generate_billboard,
    generate_mafia_reveal,
)
from cogs.casino.mafia_views import (
    DayVoteView,
    MafiaLobbyView,
    RoleRevealView,
    SecretButtonView,
)
from cogs.utility.theme import embed_to_v2, THEME, StatusPanel, lobby_embed, report_game_failure, result_embed, send_status, wait_for_view_fixed_timeout

logger = logging.getLogger(__name__)


async def poke_reminder(user, role_name, view):
    await asyncio.sleep(10)
    if not view.selected_id and not view.is_finished():
        try:
            if role_name == "mafia":
                await user.send("⏳ يا زعيم! الوقت بيجري والضحية هتهرب، خلصنا واختار حد نخلص عليه من القايمة اللي في السيرفر!")
            else:
                await user.send("⏳ يا دكتور! المريض بيموت في إيدك، انجز واختار هتعالج مين من القايمة اللي في السيرفر!")
        except asyncio.CancelledError:
            raise
        except discord.HTTPException:
            pass

# --- CORE GAME LOOP ---

class MafiaGameState:
    """Shared live state so shop items (scan/veto) can interact with a running game."""
    def __init__(self):
        self.game_name = 'mafia'
        self.alive = []
        self.mafias = []
        self.game_roles = {}


async def run_mafia_game(ctx, players):
    reminder_tasks = set()
    try:
        num = len(players)
        if num <= 10: mafia_count = 1
        elif num <= 15: mafia_count = 2
        else: mafia_count = 3
        
        doctor_count = 1
        civ_count = num - mafia_count - doctor_count
        
        roles_list = ['mafia']*mafia_count + ['doctor']*doctor_count + ['civilian']*civ_count
        random.shuffle(roles_list)
        
        game_roles = {}
        afk_tracker = {p: 0 for p in players} 
        mafias = []
        doctor = None
        
        for i, p in enumerate(players):
            role = roles_list[i]
            game_roles[p] = role
            if role == 'mafia': mafias.append(p)
            elif role == 'doctor': doctor = p
            
        alive = players.copy()

        # --- LIVE GAME STATE ---
        try:
            _state = MafiaGameState()
            _state.alive = alive
            _state.mafias = mafias
            _state.game_roles = game_roles
            if hasattr(ctx.bot, 'active_games') and ctx.channel.id in ctx.bot.active_games:
                ctx.bot.active_games[ctx.channel.id]['game_ref'] = _state
        except Exception:
            logger.exception("Mafia game state registration failed")

        reveal_view = RoleRevealView(alive, game_roles)
        reveal_view.message = await ctx.send(view=reveal_view)
        set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), reveal_view)
        await asyncio.sleep(5) 
        
        day_number = 1
        
        while True:
            embed = discord.Embed(
                description=f"🌙 **الليلة {day_number}**\nالليل دخل على الحفرة... المافيا بتتحرك.",
                color=discord.Color.dark_theme()
            )
            await ctx.send(view=embed_to_v2(embed))
            
            bb_bytes = await generate_billboard(alive)
            bb_embed = discord.Embed(color=discord.Color.dark_theme())
            bb_embed.set_image(url="attachment://billboard.jpg")
            await ctx.send(view=embed_to_v2(bb_embed), file=discord.File(fp=bb_bytes, filename="billboard.jpg"))
            await asyncio.sleep(2)

            alive_mafias = [m for m in mafias if m in alive]
            hitman = random.choice(alive_mafias) if alive_mafias else None
            
            mafia_target_id = None
            if hitman:
                try:
                    mafia_file = discord.File("mafiagame/mafia_looking.jpg", filename="mafia_looking.jpg")
                    targets = [p for p in alive if p not in alive_mafias]
                    m_view = SecretButtonView(hitman, alive_mafias, targets, "mafia")
                    
                    m_embed = discord.Embed(
                        description="الأوامر صدرت... 🔪 المافيا بتدور على ضحية دلوقتي.",
                        color=discord.Color.dark_red()
                    )
                    m_embed.set_image(url="attachment://mafia_looking.jpg")
                    
                    m_msg = await ctx.send(view=m_view, file=mafia_file)
                    m_view.message = m_msg
                    set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), m_view)
                    reminder = asyncio.create_task(poke_reminder(hitman, "mafia", m_view))
                    reminder_tasks.add(reminder)
                    reminder.add_done_callback(reminder_tasks.discard)

                    mafia_timed_out = await wait_for_view_fixed_timeout(m_view, 20.0)
                    mafia_target_id = m_view.selected_id

                    if mafia_target_id is None:
                        m_view._panel_text.content = (
                            "# انتهت مهلة حركة المافيا\n"
                            "مفيش اختيار اتسجل؛ حركة المافيا هتعدي من غير اغتيال."
                            if mafia_timed_out
                            else "# حركة المافيا اتقفلت\nمفيش اختيار اتسجل."
                        )
                        afk_tracker[hitman] += 1
                        if afk_tracker[hitman] >= 3:
                            alive.remove(hitman)
                            embed = discord.Embed(
                                description=f"⚠️ **النظام:** المافيا {hitman.mention} نام في العسل لـ 3 جولات ورا بعض! تم استبعاده من اللعبة للكسل.",
                                color=discord.Color.dark_grey()
                            )
                            await ctx.send(view=embed_to_v2(embed))
                    else:
                        afk_tracker[hitman] = 0

                    m_view.disable_controls()
                    try: await m_msg.edit(view=m_view)
                    except discord.HTTPException: pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Mafia night action failed")
                    try:
                        await ctx.send(
                            view=StatusPanel(
                                "تعذر إكمال حركة المافيا",
                                "حصل خطأ تقني أثناء اختيار الضحية. هنكمل الجولة من غير اعتماد اختيار غير مؤكد.",
                                "warning",
                            )
                        )
                    except discord.HTTPException:
                        logger.exception("Failed to report mafia action error")
            else:
                await asyncio.sleep(5)
                
            await asyncio.sleep(1)

            doctor_target_id = None
            alive_doctors = [doctor] if doctor in alive else []
            if doctor in alive:
                try:
                    doc_file = discord.File("mafiagame/doctor_looking.jpg", filename="doctor_looking.jpg")
                    d_view = SecretButtonView(doctor, alive_doctors, alive, "doctor") 
                    
                    d_embed = discord.Embed(
                        description="💉 الدكتور بيلف في الشوارع بيدور على حد يعالجه...",
                        color=discord.Color.teal()
                    )
                    d_embed.set_image(url="attachment://doctor_looking.jpg")
                    
                    d_msg = await ctx.send(view=d_view, file=doc_file)
                    d_view.message = d_msg
                    set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), d_view)
                    reminder = asyncio.create_task(poke_reminder(doctor, "doctor", d_view))
                    reminder_tasks.add(reminder)
                    reminder.add_done_callback(reminder_tasks.discard)

                    doctor_timed_out = await wait_for_view_fixed_timeout(d_view, 20.0)
                    doctor_target_id = d_view.selected_id

                    if doctor_target_id is None:
                        d_view._panel_text.content = (
                            "# انتهت مهلة حركة الدكتور\n"
                            "مفيش علاج اتسجل؛ الجولة هتكمل من غير إنقاذ."
                            if doctor_timed_out
                            else "# حركة الدكتور اتقفلت\nمفيش علاج اتسجل."
                        )
                        afk_tracker[doctor] += 1
                        if afk_tracker[doctor] >= 3:
                            alive.remove(doctor)
                            embed = discord.Embed(
                                description=f"⚠️ **النظام:** الدكتور {doctor.mention} اتأخر 3 جولات ومأنقذش حد! تم سحب رخصته واستبعاده من اللعبة.",
                                color=discord.Color.dark_grey()
                            )
                            await ctx.send(view=embed_to_v2(embed))
                    else:
                        afk_tracker[doctor] = 0
                    
                    d_view.disable_controls()
                    try: await d_msg.edit(view=d_view)
                    except discord.HTTPException: pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Doctor night action failed")
                    try:
                        await ctx.send(
                            view=StatusPanel(
                                "تعذر إكمال علاج الدكتور",
                                "حصل خطأ تقني أثناء اختيار العلاج. هنكمل الجولة من غير اعتماد اختيار غير مؤكد.",
                                "warning",
                            )
                        )
                    except discord.HTTPException:
                        logger.exception("Failed to report doctor action error")
            else:
                await asyncio.sleep(2) 
            
            await asyncio.sleep(1)

            embed = discord.Embed(
                description="🌅 **الشمس طلعت على الحفرة...**",
                color=discord.Color.gold()
            )
            await ctx.send(view=embed_to_v2(embed))
            await asyncio.sleep(1) 
            
            if mafia_target_id:
                target_obj = next((p for p in alive if p.id == mafia_target_id), None)
                
                if target_obj:
                    if doctor_target_id == mafia_target_id:
                        save_bytes = await generate_action_image("doctor_saved.jpg", target_obj, "saved_person")
                        res_embed = discord.Embed(
                            description=f"المافيا حاولت تغتال **{target_obj.mention}** بليل... بس الدكتور كان صاحي ولحقه في آخر لحظة! 💉🩸",
                            color=discord.Color.green()
                        )
                        res_embed.set_image(url="attachment://saved.jpg")
                        await ctx.send(view=embed_to_v2(res_embed), file=discord.File(fp=save_bytes, filename="saved.jpg"))
                    else:
                        alive.remove(target_obj)
                        if game_roles[target_obj] == 'doctor':
                            kill_bytes = await generate_action_image("doctor_killed.jpg", target_obj, "dead_doctor")
                            res_embed = discord.Embed(
                                description=f"المافيا غدرت بالدكتور **{target_obj.mention}**! الحفرة بقت من غير دكتور. 💀",
                                color=discord.Color.dark_theme()
                            )
                            res_embed.set_image(url="attachment://dead_doc.jpg")
                            await ctx.send(view=embed_to_v2(res_embed), file=discord.File(fp=kill_bytes, filename="dead_doc.jpg"))
                        else:
                            kill_bytes = await generate_action_image("mafia_kill.jpg", target_obj, "dead_civilian")
                            res_embed = discord.Embed(
                                description=f"المافيا طيرت المواطن **{target_obj.mention}**... ولقينا جثته مرمية في الشارع. 💀",
                                color=discord.Color.dark_theme()
                            )
                            res_embed.set_image(url="attachment://dead_civ.jpg")
                            await ctx.send(view=embed_to_v2(res_embed), file=discord.File(fp=kill_bytes, filename="dead_civ.jpg"))
            else:
                embed = discord.Embed(
                    description="الليلة دي عدت بسلام... المافيا نامت ومحدش مات. 🕊️",
                    color=discord.Color.blue()
                )
                await ctx.send(view=embed_to_v2(embed))

            await asyncio.sleep(2) 

            alive_mafias = [m for m in mafias if m in alive]
            if len(alive_mafias) == 0:
                await town_wins(ctx, mafias)
                break
            elif len(alive_mafias) >= len(alive) - len(alive_mafias):
                await mafia_wins(ctx, mafias)
                break

            bb_bytes = await generate_billboard(alive)
            bb_embed = discord.Embed(
                description="الكل يتجمع! وقت المحاكمة... قدامكم **20 ثانية** تختاروا مين المافيا وتعدموه.",
                color=discord.Color.orange()
            )
            bb_embed.set_image(url="attachment://billboard.jpg")
            await ctx.send(view=embed_to_v2(bb_embed), file=discord.File(fp=bb_bytes, filename="billboard.jpg"))
            
            vote_view = DayVoteView(alive)
            vote_msg = await ctx.send(view=vote_view)
            vote_view.message = vote_msg
            set_game_view(ctx.bot, ctx.channel.id, asyncio.current_task(), vote_view)
            await wait_for_view_fixed_timeout(vote_view, 20.0)

            vote_view.disable_controls()
            vote_view._panel_text.content = (
                "# انتهى التصويت\n"
                f"اتقفل التصويت بعد تسجيل **{len(vote_view.votes)}/{len(alive)}** أصوات. الاختيارات اتقفلت."
            )
            try: await vote_msg.edit(view=vote_view)
            except discord.HTTPException: pass
            
            tally = {}
            for voter, t_id in vote_view.votes.items():
                tally[t_id] = tally.get(t_id, 0) + 1
                
            if not tally:
                embed = discord.Embed(
                    description="محدش صوت أصلاً... اليوم عدى على الفاضي.",
                    color=discord.Color.light_grey()
                )
                await ctx.send(view=embed_to_v2(embed))
            else:
                final_tally_desc = "**📊 النتيجة النهائية للتصويت:**\n\n"
                for t_id, count in tally.items():
                    if t_id == "SKIP":
                        final_tally_desc += f"⏭️ **تفويت التصويت:** {count} أصوات\n"
                    else:
                        final_tally_desc += f"🔪 <@{t_id}>: **{count}** أصوات\n"
                        
                embed = discord.Embed(
                    title="انتهى التصويت!",
                    description=final_tally_desc,
                    color=discord.Color.blurple()
                )
                await ctx.send(view=embed_to_v2(embed))
                await asyncio.sleep(2)
                
                sorted_tally = sorted(tally.items(), key=lambda x: x[1], reverse=True)
                top_target_id = sorted_tally[0][0]
                top_votes = sorted_tally[0][1]
                
                tie = False
                if len(sorted_tally) > 1 and sorted_tally[1][1] == top_votes:
                    tie = True
                    
                if tie:
                    embed = discord.Embed(
                        description="الناس اختلفت والأصوات اتعادلت... محدش اتعدم النهاردة. ⚖️",
                        color=discord.Color.light_grey()
                    )
                    await ctx.send(view=embed_to_v2(embed))
                elif top_target_id == "SKIP":
                    embed = discord.Embed(
                        description="الأغلبية اختارت تفوت التصويت... محدش هيتعدم النهاردة. ⏭️",
                        color=discord.Color.light_grey()
                    )
                    await ctx.send(view=embed_to_v2(embed))
                else:
                    hanged_player = next((p for p in alive if p.id == top_target_id), None)
                    if hanged_player:
                        # --- VETO RESOLUTION ---
                        try:
                            cur_cog = ctx.bot.get_cog('Currency')
                        except Exception:
                            logger.exception("Could not resolve the Currency cog for the Mafia item action")
                            cur_cog = None
                        if cur_cog and await asyncio.to_thread(cur_cog.remove_item, hanged_player.id, "veto", 1):
                            embed = discord.Embed(
                                description=f"⛔ **{hanged_player.mention}** استخدم **فيتو**! الناس صوتت ضده بس الفيتو لغى الإعدام... عاش يا وحش!",
                                color=discord.Color.gold()
                            )
                            await ctx.send(view=embed_to_v2(embed))
                            await asyncio.sleep(2)
                            alive_mafias = [m for m in mafias if m in alive]
                            if len(alive_mafias) == 0:
                                await town_wins(ctx, mafias)
                                break
                            elif len(alive_mafias) >= len(alive) - len(alive_mafias):
                                await mafia_wins(ctx, mafias)
                                break
                            day_number += 1
                            continue
                        alive.remove(hanged_player)
                        r = game_roles[hanged_player]
                        ar_role = "مافيا 🔪" if r == 'mafia' else ("دكتور 💉" if r == 'doctor' else "مواطن غلبان 👤")
                        embed = discord.Embed(
                            description=f"🚨 الناس قررت تعدم **{hanged_player.mention}**! وبعد ما فتشوه اكتشفوا إنه كان: **{ar_role}**.",
                            color=discord.Color.red()
                        )
                        await ctx.send(view=embed_to_v2(embed))

            await asyncio.sleep(2) 

            alive_mafias = [m for m in mafias if m in alive]
            if len(alive_mafias) == 0:
                await town_wins(ctx, mafias)
                break
            elif len(alive_mafias) >= len(alive) - len(alive_mafias):
                await mafia_wins(ctx, mafias)
                break
                
            day_number += 1
            
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Mafia game loop failed")
        await report_game_failure(
            ctx,
            "توقفت المافيا بسبب خطأ",
            "حصل عطل غير متوقع أثناء اللعبة. تم تسجيل الخطأ، وتم إيقاف الجولة بدل ما تستمر بحالة غير مضمونة.",
        )
    finally:
        for reminder in reminder_tasks:
            reminder.cancel()
        if reminder_tasks:
            await asyncio.gather(*reminder_tasks, return_exceptions=True)
        release_game_task(ctx.bot, ctx.channel.id, asyncio.current_task())

async def town_wins(ctx, mafias):
    reveal_bytes = await generate_mafia_reveal(mafias)
    embed = result_embed(
        title="🎉 المواطنين انتصروا!",
        description="المواطنين قدروا ينضفوا الحفرة من المافيا! النور رجع للمدينة.",
        outcome="win",
    )
    embed.set_image(url="attachment://reveal.jpg")
    await ctx.send(view=embed_to_v2(embed), file=discord.File(fp=reveal_bytes, filename="reveal.jpg"))

async def mafia_wins(ctx, mafias):
    reveal_bytes = await generate_mafia_reveal(mafias)
    embed = result_embed(
        title="💀 المافيا سيطرت!",
        description="المافيا مسكت الحفرة بالكامل... المواطنين ماتوا والظلام كسب.",
        outcome="loss",
    )
    embed.set_image(url="attachment://reveal.jpg")
    await ctx.send(view=embed_to_v2(embed), file=discord.File(fp=reveal_bytes, filename="reveal.jpg"))

# --- COG COMMANDS ---
class Mafia(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='مافيا', description='بدء لعبة المافيا')
    async def start_mafia(self, ctx):
        """يبدأ لعبة المافيا في الحفرة"""
        if is_active_game(self.bot, ctx.channel.id):
            return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ واحدة جديدة.",
                "warning",
            )
        view = None
        try:
            menu_file = discord.File("mafiagame/menu.jpg", filename="menu.jpg")
            view = MafiaLobbyView(ctx, start_game=run_mafia_game)
            if not register_game_task(self.bot, ctx.channel.id, view.timer_task, ctx.author.id, "Mafia lobby", view=view):
                view.timer_task.cancel()
                return await send_status(
                ctx,
                "الروم مشغول",
                "فيه لعبة أو لوبي شغال في الروم بالفعل. استنى الجولة الحالية تخلص قبل ما تبدأ واحدة جديدة.",
                "warning",
            )
            view.message = await ctx.send(view=view, file=menu_file)
            if not owns_game_task(self.bot, ctx.channel.id, view.timer_task):
                view._disable_buttons()
                await view.message.edit(view=view)
        except Exception:
            if view is not None:
                release_game_task(self.bot, ctx.channel.id, view.timer_task)
                view.timer_task.cancel()
            logger.exception(
                "Could not create Mafia lobby requester_id=%s channel_id=%s",
                ctx.author.id,
                ctx.channel.id,
            )
            send_kwargs = {"ephemeral": True} if ctx.interaction is not None else {}
            try:
                await ctx.send(
                    view=StatusPanel(
                        "تعذر فتح لوبي المافيا",
                        "حصل عطل أثناء تجهيز واجهة اللعبة أو تحميل ملفاتها. سجلنا التفاصيل؛ جرّب تاني بعد شوية.",
                        "warning",
                    ),
                    **send_kwargs,
                )
            except (discord.HTTPException, discord.InteractionResponded):
                logger.exception(
                    "Could not send Mafia startup error panel requester_id=%s channel_id=%s",
                    ctx.author.id,
                    ctx.channel.id,
                )

    @commands.hybrid_command(name='اكشف', aliases=['scan', 'كشف'], description='استخدم أداة كشف المافيا')
    async def mafia_scan(self, ctx):
        """Use one scan item and deliver all investigation details privately."""
        async def respond(title, description, kind="warning"):
            kwargs = {"ephemeral": True} if getattr(ctx, "interaction", None) is not None else {}
            return await ctx.send(
                view=StatusPanel(title, description, kind),
                **kwargs,
            )

        cur_cog = self.bot.get_cog("Currency")
        if cur_cog is None:
            return await respond("الاقتصاد غير متاح", "مركز الاقتصاد مش محمّل حاليًا. جرّب تاني بعد إصلاح البوت.")
        if await asyncio.to_thread(cur_cog.get_item_count, ctx.author.id, "mafia_scan") <= 0:
            return await respond(
                "أداة الكشف مش موجودة",
                "مفيش كشف مافيا في شنطتك. تقدر تشتريه من المتجر داخل مركز الاقتصاد.",
            )
        active_games = getattr(self.bot, "active_games", {})
        game_data = active_games.get(ctx.channel.id)
        if game_data is None:
            return await respond("مفيش لعبة مافيا", "ابدأ لعبة مافيا في الروم ده الأول.")
        game = game_data.get("game_ref")
        if game is None or getattr(game, "game_name", "") != "mafia":
            return await respond("الأمر خاص بالمافيا", "أداة الكشف بتشتغل داخل جولة مافيا نشطة بس.")
        if ctx.author not in game.alive:
            return await respond("أنت خارج الجولة", "اللاعبون اللي خرجوا من الجولة مش بيقدروا يستخدموا أدواتها.")
        alive_mafias = [member for member in game.mafias if member in game.alive and member != ctx.author]
        if not alive_mafias:
            return await respond("مفيش هدف صالح", "مفيش مافيا تانية عايشة تقدر تكشفها دلوقتي.")
        if not await asyncio.to_thread(cur_cog.remove_item, ctx.author.id, "mafia_scan", 1):
            return await respond(
                "تعذر استخدام الأداة",
                "الأداة اتستخدمت أو مش موجودة. اتأكد من شنطتك وحاول تاني.",
            )

        hint = random.choice(alive_mafias)
        masked = hint.display_name[:2] + "***"
        result_panel = discord.Embed(
            title="🔍 نتيجة الكشف (سري)",
            description=(
                f"الجهاز بيقول إن **{masked}** ({hint.mention}) عليه حركات مافيا... 👀\n"
                "(اتخصم منك 1 كشف)"
            ),
            color=discord.Color.gold(),
        )
        try:
            await ctx.author.send(
                view=embed_to_v2(result_panel),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            try:
                await asyncio.to_thread(cur_cog.add_item, ctx.author.id, "mafia_scan", 1)
            except Exception:
                logger.exception(
                    "Failed to refund mafia_scan after DM Forbidden user_id=%s",
                    ctx.author.id,
                )
                return await respond(
                    "تعذر استرداد الأداة",
                    "الخاص مقفول وفشل استرداد أداة الكشف تلقائيًا. تواصل مع إدارة السيرفر قبل استخدام أداة أخرى.",
                )
            return await respond(
                "افتح الرسائل الخاصة",
                "مش قادر أبعت نتيجة الكشف في الخاص، ورجّعنا الأداة لشنطتك.",
            )
        except discord.HTTPException:
            try:
                await asyncio.to_thread(cur_cog.add_item, ctx.author.id, "mafia_scan", 1)
            except Exception:
                logger.exception(
                    "Failed to refund mafia_scan after DM HTTP error user_id=%s",
                    ctx.author.id,
                )
                return await respond(
                    "تعذر استرداد الأداة",
                    "فشل إرسال النتيجة واسترداد الأداة تلقائيًا. تواصل مع إدارة السيرفر قبل استخدام أداة أخرى.",
                )
            logger.warning(
                "Could not deliver Mafia scan via DM; refunded item user_id=%s",
                ctx.author.id,
                exc_info=True,
            )
            return await respond(
                "تعذر إرسال النتيجة",
                "ما قدرناش نبعت النتيجة في الخاص، ورجّعنا الأداة لشنطتك.",
            )

        return await respond(
            "النتيجة اتبعتت في الخاص",
            "راجع رسائلك الخاصة لمعرفة نتيجة الكشف. تفاصيل الهدف لا تظهر في شات اللعبة.",
            "success",
        )

async def setup(bot):
    await bot.add_cog(Mafia(bot))