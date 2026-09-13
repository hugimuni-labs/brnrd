from PIL import Image, ImageDraw, ImageFont
W,H=1000,1300
BG="#070a0d"; PANEL="#0d1317"; LINE="#253238"; MUTED="#72848a"; PHOS="#c9eff0"; AMBER="#ffae24"; PALE="#ffd47b"; GHOST="#1a2328"; GHOST2="#121a1e"
F="/System/Library/Fonts/Menlo.ttc"
def font(s,b=False): return ImageFont.truetype(F,s,index=1 if b else 0)
im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
def panel(x,y,w,h,title):
    d.rounded_rectangle((x,y,x+w,y+h),radius=10,fill=PANEL,outline=LINE,width=2)
    d.rectangle((x,y,x+w,y+34),fill="#0a1013"); d.line((x,y+34,x+w,y+34),fill=LINE)
    for i,c in enumerate(("#3a4a52","#3a4a52","#3a4a52")): d.ellipse((x+14+i*20,y+11,x+26+i*20,y+23),fill=c)
    d.text((x+w/2,y+17),title,fill=MUTED,font=font(15),anchor="mm")
def block(x,y,w,role,lines,color,fill,ghost=False):
    h=18+len(lines)*22
    d.rounded_rectangle((x,y,x+w,y+h),radius=6,fill=fill)
    if not ghost:
        d.text((x+12,y+8),role,fill=color,font=font(13,True))
        for i,l in enumerate(lines): d.text((x+12,y+8+ (i+1)*22 - 4),l,fill=color,font=font(14))
    else:
        for i in range(len(lines)+1):
            ln=w-40-(i*37)%120
            d.rounded_rectangle((x+12,y+12+i*22,x+12+ln,y+12+i*22+8),radius=4,fill=GHOST)
    return h
# LEFT — close it, it forgets
lx,ly,lw,lh=30,30,940,620
panel(lx,ly,lw,lh,"agent — session")
y=ly+56
asks=[("you",["fix the flaky driver on the fuel deck,","then the same for the other two"],PALE,"#14202a"),
      ("agent",["Reading drive-fuel.mjs… the reads go through","page.evaluate; a navigation can destroy the ctx.","Converting to locators."],PHOS,"#0f171b"),
      ("agent",["8/8 green. Opening the PR."],PHOS,"#0f171b"),
      ("you",["great — now the other two"],PALE,"#14202a")]
for i,(r,l,c,f) in enumerate(asks):
    # fade top-down: earliest most faded
    fade = i<3
    h=block(lx+24,y,lw-48,r,l,c,f,ghost=fade); y+=h+14
# the cut: session ended
d.line((lx+24,y+30,lx+lw-24,y+30),fill=LINE,width=2)
d.text((lx+lw/2,y+56),"session ended",fill=MUTED,font=font(16),anchor="mm")
d.text((lx+lw/2,y+80),"context: 0 / 200k",fill=MUTED,font=font(14),anchor="mm")
y+=104
h=block(lx+24,y,lw-48,"you",["…the other two?"],PALE,"#14202a"); y+=h+14
h=block(lx+24,y,lw-48,"agent",["Which two? Could you share the files?"],PHOS,"#0f171b"); y+=h+14
d.text((lx+lw/2,ly+lh-28),"close it — it forgets",fill=AMBER,font=font(18,True),anchor="mm")
# RIGHT — keep it open, it buries you
rx,ry,rw,rh=30,680,940,590
panel(rx,ry,rw,rh,"agent — session · 41 turns")
y=ry+56
h=block(rx+24,y,rw-48,"you",["fix the flaky driver on the fuel deck"],PALE,"#14202a"); y+=h+10
import random; random.seed(7)
n=0
while y<ry+rh-120:
    r = "you" if n%3==2 else "agent"
    lines = [["Allow `npm test`? [y/n]"],["Running… ✓ 1/8"],["Allow write to repro/drive-fuel.mjs? [y/n]"],["Should I also update the snapshot? [y/n]"],["Ran 3/8, one red — retrying"],["Allow `git push`? [y/n]"],["Need your input: keep the 700ms delay?"],["…waiting for approval"]][n%8]
    if r=="you": lines=random.choice([["y"],["yes"],["ok"],["no, keep going"]])
    c = PALE if r=="you" else PHOS; f = "#14202a" if r=="you" else "#0f171b"
    h=block(rx+24,y,rw-48,r,lines,c,f); y+=h+10; n+=1
# scrollbar + fade at bottom
d.rectangle((rx+rw-14,ry+40,rx+rw-8,ry+rh-40),fill=GHOST2); d.rectangle((rx+rw-14,ry+rh-140,rx+rw-8,ry+rh-40),fill=LINE)
fade=Image.new("RGBA",(rw-18,140),(13,19,23,0)); fd=ImageDraw.Draw(fade)
for i in range(140):
    fd.line((0,i,rw-18,i),fill=(13,19,23,min(255,int(255*(i/90)))))
im.paste(fade,(rx+2,ry+rh-180),fade)
d.text((rx+rw/2,ry+rh-28),"keep it open — it buries you",fill=AMBER,font=font(18,True),anchor="mm")
im.save("/tmp/v12/slide2-lose.png"); print("slide2-lose.png", im.size)
