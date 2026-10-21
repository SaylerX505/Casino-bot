import discord, asyncio, random, io, math
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from cogs.utility.theme import THEME, WatermarkView, lobby_embed, base_embed, get_currency
from cogs.casino.permissions import is_staff_or_owner
from cogs.casino.game_registry import is_active_game, owns_game_task, register_game_task, release_game_task
FEE = 50
COIN = "<:ckrcoin:1513743032124575784>"
SEG = [("+100",100),("-50",-50),("x2","x2"),("BOX","box"),("0",0),("+150",150),("-30",-30),("+70",70)]
COLS = [(245,197,66),(42,166,164),(139,30,30),(46,204,113),(230,126,34),(155,89,182)]
def pic(k=None):
 s=600;i=Image.new("RGBA",(s,s),(15,15,15,255));d=ImageDraw.Draw(i);c=s//2;r=260;n=len(SEG)
 for x,(lb,_v) in enumerate(SEG):
  a0=(360/n)*x-90;a1=(360/n)*(x+1)-90;cc=(255,255,255) if k==x else COLS[x%len(COLS)]
  d.pieslice([c-r,c-r,c+r,c+r],start=a0,end=a1,fill=cc+(255,),outline=(0,0,0,255),width=3)
  m=math.radians((a0+a1)/2)
  try:f=ImageFont.truetype("arial.ttf",22)
  except:f=ImageFont.load_default()
  d.text((c+int(math.cos(m)*160),c+int(math.sin(m)*160)),lb,fill=(0,0,0,255),font=f,anchor="mm")
 d.ellipse([c-40,c-40,c+40,c+40],fill=(57,58,65,255),outline=(245,197,66,255),width=4)
 d.polygon([(c-18,8),(c+18,8),(c,45)],fill=(255,60,60,255))
 b=io.BytesIO();i.save(b,format="PNG");b.seek(0);return b
class WL(discord.ui.View):
 def __init__(s,ctx):
  super().__init__(timeout=None);s.ctx=ctx;s.pl=[];s.st=False;s.t=35;s.msg=None
  s.tt=s.ctx.bot.loop.create_task(s.tick())
 def emb(s):
  return lobby_embed("🎡 عجلة الحفرة","**ازاي تلعب:**\n1- دخول بـ **50**\n2- لف واكسب لحد **x2**\n\n",s.pl,s.t)
 async def tick(s):
  while s.t>0 and not s.st:
   await asyncio.sleep(5)
   if s.st:break
   s.t-=5
   if s.t<=0:await s.go()
   elif s.msg:
    try:await s.msg.edit(embed=s.emb())
    except:pass
 async def go(s):
  if s.st:return
  if len(s.pl)<1:
   s.st=True;await s.ctx.send(embed=base_embed(description="❌ مفيش حد!",color=THEME["red_dark"]))
   for c in s.children:c.disabled=True
   if s.msg:await s.msg.edit(view=s)
   return
  s.st=True
  for c in s.children:c.disabled=True
  if s.msg:
   try:await s.msg.edit(view=s)
   except:pass
  t=s.ctx.bot.loop.create_task(s.run())
  if not register_game_task(s.ctx.bot,s.ctx.channel.id,t,s.ctx.author.id,"Wheel",replace_task=s.tt):
   t.cancel()
 async def run(s):
  try:
   cur=get_currency(s.ctx.bot)
   for p in list(s.pl):
    if cur:
     fee_paid,_balance_after_fee=cur.debit_balance(p.id,FEE)
     if not fee_paid:
      await s.ctx.send(embed=base_embed(description=f"❌ {p.mention} فلوسك ناقصة!",color=THEME["red_dark"]));s.pl.remove(p);continue
    idx=random.randrange(len(SEG));lb,v=SEG[idx];buf=pic(idx);f=discord.File(fp=buf,filename="w.png");g=0;ex=""
    if isinstance(v,int):g=v
    elif v=="x2":g=FEE*2
    else:g=random.choice([20,50,80,120,200]);ex=f"\n🎁 صندوق **{g:,}** {COIN}!"
    if cur and g > 0:
     cur.update_balance(p.id,g)
    elif cur and isinstance(v,int) and v < 0:
     actual_loss,_balance_after_loss=cur.charge_up_to(p.id,-g)
     g=-actual_loss
    b=cur.get_balance(p.id) if cur else 0
    if g>0:tx=f"🎉 {p.mention} **{lb}**!\n💰 **{g:,}**{ex}\n🏦 **{b:,}**";co=THEME["gold"]
    elif isinstance(v,int) and v<0:tx=f"😅 {p.mention} **{lb}**!\n💸 خسر **{abs(g):,}**\n🏦 **{b:,}**";co=THEME["red_dark"]
    else:tx=f"😶‍🌫️ {p.mention} **{lb}**!";co=THEME["grey"]
    e=base_embed(title="🎡 لفة",description=tx,color=co);e.set_image(url="attachment://w.png")
    await s.ctx.send(embed=e,file=f,view=WatermarkView());await asyncio.sleep(2)
   await s.ctx.send(embed=base_embed(title="🏁 خلصت!",description="تسلم ايديكم!",color=THEME["green"]),view=WatermarkView())
  except asyncio.CancelledError:pass
  finally:
   release_game_task(s.ctx.bot,s.ctx.channel.id,asyncio.current_task())
 @discord.ui.button(label="انضمام 🎡",style=discord.ButtonStyle.success)
 async def j(s,i,b):
  if s.st:return await i.response.send_message(embed=base_embed(description="بدأت!",color=THEME["red_dark"]),ephemeral=True)
  if i.user in s.pl:return await i.response.send_message(embed=base_embed(description="مسجل!",color=THEME["red_dark"]),ephemeral=True)
  s.pl.append(i.user);await i.response.edit_message(embed=s.emb())
 @discord.ui.button(label="مغادرة",style=discord.ButtonStyle.danger)
 async def l(s,i,b):
  if i.user in s.pl:s.pl.remove(i.user);await i.response.edit_message(embed=s.emb())
  else:await i.response.send_message(embed=base_embed(description="مش فيها!",color=THEME["red_dark"]),ephemeral=True)
 @discord.ui.button(label="ابدأ",style=discord.ButtonStyle.primary)
 async def st(s,i,b):
  if i.user!=s.ctx.author:return await i.response.send_message(embed=base_embed(description="الهوست بس!",color=THEME["red_dark"]),ephemeral=True)
  await i.response.defer();s.t=0;await s.go()
class Wheel(commands.Cog):
 def __init__(s,b):s.bot=b
 @commands.command(name="عجلة",aliases=["عجله","wheel"])
 @is_staff_or_owner()
 async def w(s,ctx):
  if is_active_game(s.bot,ctx.channel.id):return await ctx.send(embed=base_embed(description="❌ فيه لعبة أو لوبي!",color=THEME["red_dark"]))
  v=WL(ctx)
  if not register_game_task(s.bot,ctx.channel.id,v.tt,ctx.author.id,"Wheel lobby",view=v):
   v.tt.cancel()
   return await ctx.send(embed=base_embed(description="❌ فيه لعبة أو لوبي!",color=THEME["red_dark"]))
  try:
   v.msg=await ctx.send(embed=v.emb(),view=v)
   v.message=v.msg
  except Exception:
   release_game_task(s.bot,ctx.channel.id,v.tt);v.tt.cancel();raise
  if not owns_game_task(s.bot,ctx.channel.id,v.tt):
   for child in v.children:child.disabled=True
   await v.message.edit(view=v)
async def setup(b):await b.add_cog(Wheel(b))

