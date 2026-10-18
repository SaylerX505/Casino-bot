import discord, re
from discord.ext import commands
from cogs.utility.theme import THEME, WatermarkView, base_embed, get_currency
COIN="<:ckrcoin:1513743032124575784>"
NM={"🪨":"حجر","📄":"ورقة","✂️":"مقص"}
BT={"🪨":"✂️","📄":"🪨","✂️":"📄"}
class V(discord.ui.View):
 def __init__(s,ctx,a,o,bet):
  super().__init__(timeout=60);s.ctx=ctx;s.a=a;s.o=o;s.bet=bet;s.pk={};s.done=False
 def emb(s):
  ca="✅" if s.a.id in s.pk else "⏳";oa="✅" if s.o.id in s.pk else "⏳"
  pot=int(s.bet*2*0.95)
  return discord.Embed(title="✊ حومص!",description=f"{s.a.mention} VS {s.o.mention}\n💰 رهان **{s.bet:,}** - الفايز **{pot:,}**\n\n{s.a.mention}: {ca}\n{s.o.mention}: {oa}",color=THEME["teal"])
 async def fin(s,inter=None):
  if s.done:return
  s.done=True
  for c in s.children:c.disabled=True
  cur=get_currency(s.ctx.bot);pot=int(s.bet*2*0.95);tax=int(s.bet*2*0.05)
  a=s.pk.get(s.a.id);o=s.pk.get(s.o.id)
  if a is None or o is None:
   if cur:cur.update_balance(s.a.id,s.bet);cur.update_balance(s.o.id,s.bet)
   e=base_embed(description="⏰ حد اتأخر! الفلوس رجعت.",color=THEME["orange"])
   try:
    if inter:await inter.response.edit_message(embed=e,view=s)
    else:await s.ctx.send(embed=e)
   except:pass
   return
  if a==o:
   if cur:cur.update_balance(s.a.id,s.bet);cur.update_balance(s.o.id,s.bet)
   e=base_embed(title="🤝 تعادل!",description=f"انتو الاتنين **{NM[a]}**! الفلوس رجعت.",color=THEME["grey"])
  else:
   w=s.a if BT[a]==o else s.o;wp=a if BT[a]==o else o;lp=o if BT[a]==o else a
   if cur:cur.update_balance(w.id,pot)
   e=base_embed(title="🏆 فايز!",description=f"**{NM[wp]}** يكسب **{NM[lp]}**\n🎉 {w.mention} خد **{pot:,}** {COIN}!\n🧾 ضريبة: **{tax:,}**",color=THEME["gold"])
   e.set_thumbnail(url=w.display_avatar.url)
  try:
   if inter and not inter.response.is_done():await inter.response.edit_message(embed=e,view=s)
   else:await s.ctx.send(embed=e,view=WatermarkView())
  except:await s.ctx.send(embed=e,view=WatermarkView())
 async def on_timeout(s):await s.fin()
 async def pk2(s,i,ch):
  if i.user.id not in (s.a.id,s.o.id):return await i.response.send_message("مش طرف!",ephemeral=True)
  if i.user.id in s.pk:return await i.response.send_message("اختارت خلاص!",ephemeral=True)
  s.pk[i.user.id]=ch;await i.response.send_message(f"اختارت **{NM[ch]}** 🤫",ephemeral=True)
  if len(s.pk)==2:await s.fin(i)
  else:
   try:await i.message.edit(embed=s.emb())
   except:pass
 @discord.ui.button(emoji="🪨",label="حجر",style=discord.ButtonStyle.secondary)
 async def r(s,i,b):await s.pk2(i,"🪨")
 @discord.ui.button(emoji="📄",label="ورقة",style=discord.ButtonStyle.primary)
 async def p(s,i,b):await s.pk2(i,"📄")
 @discord.ui.button(emoji="✂️",label="مقص",style=discord.ButtonStyle.danger)
 async def sc(s,i,b):await s.pk2(i,"✂️")
class RPS(commands.Cog):
 def __init__(s,b):s.bot=b
 @commands.command(name="حومص",aliases=["rps","حجرة","مقص"])
 async def rps(s,ctx,bet=None,opp:discord.Member=None):
  try:amt=int(re.sub(r"\D","",bet or ""))
  except:amt=0
  if amt<10:return await ctx.send(embed=base_embed(description=f"❌ مثال: `{ctx.prefix}حومص 100 @صاحبك` (اقل 10)",color=THEME["red_dark"]))
  if amt>5000:return await ctx.send(embed=base_embed(description="❌ اقصى 5000!",color=THEME["red_dark"]))
  if opp is None:return await ctx.send(embed=base_embed(description="❌ منشن خصمك!",color=THEME["red_dark"]))
  if opp.id==ctx.author.id:return await ctx.send(embed=base_embed(description="❌ مش هتلاعب نفسك!",color=THEME["red_dark"]))
  if opp.bot:return await ctx.send(embed=base_embed(description="❌ البوتات مابتلعبش!",color=THEME["red_dark"]))
  cur=get_currency(s.bot)
  if cur is None:return await ctx.send(embed=base_embed(description="❌ الفلوس واقعة! كلم نجلاوي.",color=THEME["red_dark"]))
  if not cur.place_bet(ctx.author.id,opp.id,amt):
   a_balance=cur.get_balance(ctx.author.id);o_balance=cur.get_balance(opp.id)
   if a_balance<amt:return await ctx.send(embed=base_embed(description=f"❌ رصيدك **{a_balance}** محتاج **{amt}**!",color=THEME["red_dark"]))
   if o_balance<amt:return await ctx.send(embed=base_embed(description=f"❌ {opp.mention} فلوسه ناقصة!",color=THEME["red_dark"]))
   return await ctx.send(embed=base_embed(description="❌ الرصيد اتغير أثناء تسجيل الرهان. راجع الرصيد وحاول تاني.",color=THEME["red_dark"]))
  v=V(ctx,ctx.author,opp,amt)
  await ctx.send(content=f"{opp.mention} ⚔️ {ctx.author.mention} بيتحداك على **{amt}** {COIN}!",embed=v.emb(),view=v)
async def setup(b):await b.add_cog(RPS(b))
