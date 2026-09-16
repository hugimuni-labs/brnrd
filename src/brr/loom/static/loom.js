/* The dungeon. The feed owns facts; the hand owns camera, selection and doors.
 * Canvas is redrawn on input or a reading. Animation runs only for a new bead.
 */
const $ = id => document.getElementById(id);
const canvas = $('map'), ctx = canvas.getContext('2d');
const fixture = new URLSearchParams(location.search).get('fixture');
const fixtures = new Set(['live', 'empty', 'eighty']);
const colors = { ink:'#0b0906', line:'#3a3328', text:'#e8dcc0', dim:'#b3a78e', ice:'#8fd3ff', amber:'#f2b134' };
const fixed = ['shed','forge','wire','crew','clock','archive','pack','belt'];
const symbols = {decision:'◆', preparation:'◇', action:'●', goal:'◎'};
const collator = new Intl.Collator('en', {numeric:true});
const array = value => Array.isArray(value) ? value : [];
const number = value => typeof value === 'number' && Number.isFinite(value);
const timestamp = value => number(value) ? value * 1000 : Date.parse(value);
const hash = text => { let n = 2166136261; for (const c of text) n = Math.imul(n ^ c.charCodeAt(0),16777619); return n >>> 0; };
const duration = seconds => {
  if (!number(seconds)) return '?';
  const m = Math.max(0,Math.ceil(seconds / 60)), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m % 60}m` : `${m}m`;
};
const compact = n => number(n) ? n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n) : '?';
const bytes = n => number(n) ? `${(n/1024).toFixed(1)} KB` : '?';
function element(tag, text, className) { const e = document.createElement(tag); if (text != null) e.textContent = text; if (className) e.className = className; return e; }
function placeOf(bead) { const p = bead?.place || bead?.place_kind; return p === 'file' || p === 'home' ? 'archive' : p; }
let state = {}, rooms = [], roomById = new Map(), edges = [], wings = [], selected = 'shed';
let opened = new Set(), camera = {x:0,y:0,z:1}, width = 1, height = 1, actor = 'shed', actorPoint = null;
let stepTimer = null;
let lastBead = null, pulse = null, frame = 0, footprints = [], history = [], pageGeneration = 0, archiveIndex = 0;
let archiveExpanded = new Set(['repo','home']), archiveRows = [], initialized = false, layoutStore = {};
const referenceNow = () => fixture ? timestamp(state.at) : Date.now();
const visited = item => Number.isFinite(timestamp(item.visited_at)) && referenceNow() - timestamp(item.visited_at) <= 7*86400000;
function loadLayout() {
  try { layoutStore = JSON.parse(localStorage.getItem(`dungeon.slots.v2:${state.repo || 'empty'}`) || '{}'); } catch { layoutStore = {}; }
  if (!layoutStore || typeof layoutStore !== 'object' || Array.isArray(layoutStore)) layoutStore = {};
}
function slot(wing, id) {
  const key = `wing:${wing}`, slots = layoutStore[key] ||= {};
  if (Number.isInteger(slots[id]) && slots[id] >= 0) return slots[id];
  const used = new Set(Object.values(slots));
  let n = wing === '__wings' ? 0 : hash(id) % 6;
  while (used.has(n)) n++;
  slots[id] = n;
  return n;
}
function buildMap() {
  rooms = []; edges = []; wings = [];
  const add = r => { rooms.push(r); return r; };
  // A central court, not a row of tiles: the eight measured places never move.
  fixed.forEach((id,i) => {
    const angle=-Math.PI/2+i*Math.PI/4;
    add({id,kind:'hub',title:id,x:Math.cos(angle)*130,y:Math.sin(angle)*130,
      w:id==='shed'?94:id==='archive'?96:85,h:id==='shed'?54:42});
  });
  const crew=rooms.find(r=>r.id==='crew');
  array(state.hud?.strands).forEach((strand,i)=>{
    add({id:`strand:${strand.id}`,title:strand.title||strand.id,kind:'strand',strand,
      x:-44+(i%2)*88,y:25+Math.floor(i/2)*52,w:80,h:42});
    edges.push({from:'crew',to:`strand:${strand.id}`,kind:'passage'});
  });
  const items=array(state.warp?.items).filter(i=>i?.id&&i.state!=='retired'&&
    (i.state!=='done'||!Number.isFinite(timestamp(i.done_at||i.receipt?.at))||referenceNow()-timestamp(i.done_at||i.receipt?.at)<=7*86400000));
  const goals=array(state.warp?.goals).map(g=>({...g,type:'goal'}));
  const topics=array(state.heddles).map(h=>h.slug).filter(Boolean);
  for(const i of [...items,...goals]){const topic=array(i.topics)[0]||'unnamed';if(!topics.includes(topic))topics.push(topic);}
  const activeTopics=topics.filter(t=>[...items,...goals].some(i=>(array(i.topics)[0]||'unnamed')===t||array(i.topics).includes(t)));
  activeTopics.forEach(topic=>{
    const wingIndex=slot('__wings',topic),angle=(wingIndex%6)*Math.PI/3;
    const offset=Math.floor(wingIndex/6)*2000;
    const point=(radius,lateral=0)=>({x:Math.cos(angle)*(radius+offset)-Math.sin(angle)*lateral,y:Math.sin(angle)*(radius+offset)+Math.cos(angle)*lateral});
    const wing={id:`wing:${topic}`,slug:topic,title:topic,...point(186),w:100,h:28,kind:'wing',angle,offset,extent:480};
    wings.push(wing);add(wing);
    const mouth=rooms.filter(r=>r.kind==='hub').sort((a,b)=>Math.hypot(a.x-wing.x,a.y-wing.y)-Math.hypot(b.x-wing.x,b.y-wing.y))[0];
    edges.push({from:mouth.id,to:wing.id,kind:'passage'});
    const members=[...items,...goals].filter(i=>(array(i.topics)[0]||'unnamed')===topic).sort((a,b)=>collator.compare(a.id,b.id));
    for(const item of members){
      const n=slot(topic,item.id),goal=item.type==='goal',decision=item.type==='decision';
      const depth=goal?1320+Math.floor(n/2)*130:278+Math.floor(n/2)*142;
      const side=goal?0:(n%2?1:-1)*(48+(hash(item.id)%3)*7);
      const size=goal?[172,104]:decision?[138,76]:item.type==='preparation'?[88,50]:[96+(hash(item.id)%3)*8,54];
      add({id:item.id,title:item.title||item.id,item,kind:'item',wing:topic,...point(depth,side),w:size[0],h:size[1]});
      wing.extent=Math.max(wing.extent,depth+115);
      const needs=array(item.needs).filter(id=>items.some(i=>i.id===id));
      if(needs.length)needs.forEach(id=>edges.push({from:id,to:item.id,kind:'needs'}));
      else edges.push({from:wing.id,to:item.id,kind:'passage'});
    }
    for(const item of items.filter(i=>array(i.topics).slice(1).includes(topic))){
      const n=slot(topic,`ref:${item.id}`),depth=278+Math.floor(n/2)*142;
      add({id:`ref:${topic}:${item.id}`,target:item.id,title:`↗ ${item.id}`,kind:'reference',wing:topic,...point(depth,n%2?60:-60),w:78,h:34});
      wing.extent=Math.max(wing.extent,depth+100);
      edges.push({from:wing.id,to:`ref:${topic}:${item.id}`,kind:'passage'});
    }
  });
  roomById=new Map(rooms.map(r=>[r.id,r]));
  fixed.forEach((id,i)=>edges.push({from:id,to:fixed[(i+1)%fixed.length],kind:'passage'}));
  try{localStorage.setItem(`dungeon.slots.v2:${state.repo||'empty'}`,JSON.stringify(layoutStore));}catch{/* An unavailable browser store is not a missing world. */}
  if(!roomById.has(selected))selected='shed';
  const select=$('wing'),old=select.value;select.replaceChildren(element('option','Wings'));select.firstChild.value='';
  for(const wing of wings){const option=element('option',wing.slug);option.value=wing.id;select.append(option);}select.value=old;
  $('count').textContent=`${items.length} rooms · ${wings.length} wings`;
  $('access').replaceChildren();
  for(const r of rooms){const b=element('button',`${r.id} ${r.title}`);b.onclick=()=>{choose(r.id);activate(r);};$('access').append(b);}
}
function blockers(room) {
  return array(room.item?.needs).filter(id => roomById.get(id)?.item?.state !== 'done');
}
function locked(room) {
  if (!room.item || opened.has(room.id)) return false;
  return room.item.state === 'held' || blockers(room).length > 0;
}
function opens(room) {
  if (!room.item) return [];
  return [...new Set([...array(room.item.opens), ...array(state.warp?.items).filter(i=>array(i.needs).includes(room.id)).map(i=>i.id)])];
}
function ownership(item) { return item?.type==='decision' ? 'his' : item?.type==='goal' ? 'goal' : 'mine'; }
function hubDetail(id) {
  const beads = array(state.beads), last = [...beads].reverse().find(b=>placeOf(b)===id);
  if (id==='shed') return state.shuttle?.state || 'no seat reading';
  if (id==='archive') return `${archivePlaces().length} knotted places`;
  if (id==='crew') return state.hud?.strands == null ? 'unmeasured' : `${array(state.hud.strands).length} strands`;
  if (id==='clock'&&state.hud?.full?.schedule?.armed) return `${state.hud.full.schedule.armed.length} armed wakes`;
  if (id==='pack') return state.pack?.blocks ? `${state.pack.blocks.length} blocks` : 'unmeasured';
  if (id==='belt') return state.fuel?.buckets ? `${state.fuel.buckets.length} buckets` : 'unmeasured';
  const relic = array(state.relics).filter(r=>r.place===id).at(-1);
  return relic ? `${relic.kind} #${relic.number ?? ''} ${relic.action || ''}` : last ? `${last.act || 'act'} recorded` : 'no recorded act';
}
function drawText(text,x,y,color=colors.text,size=12,maxWidth=Infinity) {
  ctx.fillStyle=color;ctx.font=`${size}px ui-monospace, SFMono-Regular, Consolas, monospace`;
  let s=String(text ?? '');
  if(ctx.measureText(s).width>maxWidth) { while(s.length && ctx.measureText(`${s}…`).width>maxWidth) s=s.slice(0,-1);s+='…'; }
  ctx.fillText(s,x,y);
}
function wrap(text,x,y,maxWidth,lines=2,color=colors.text,size=12) {
  ctx.font=`${size}px ui-monospace, SFMono-Regular, Consolas, monospace`;
  const words=String(text || '').split(/\s+/);let line='',n=0;
  while(words.length && n<lines) { const word=words.shift(); if(line && ctx.measureText(`${line} ${word}`).width>maxWidth) { drawText(line,x,y+n*18,color,size,maxWidth);n++;line=word; } else line+=`${line?' ':''}${word}`; }
  if(n<lines) drawText(line+(words.length?'…':''),x,y+n*18,color,size,maxWidth);
}
function roundRect(x,y,w,h,r=5) {ctx.beginPath();ctx.roundRect(x,y,w,h,r);}
function roomPoint(id) { const r=roomById.get(id);return r ? {x:r.x,y:r.y} : null; }
function chamber(r){
  const l=r.x-r.w/2,t=r.y-r.h/2,c=r.item?.type==='goal'?18:r.kind==='hub'?9:5;
  ctx.beginPath();ctx.moveTo(l+c,t);ctx.lineTo(l+r.w-c,t);ctx.lineTo(l+r.w,t+c);
  ctx.lineTo(l+r.w,t+r.h-c);ctx.lineTo(l+r.w-c,t+r.h);ctx.lineTo(l+c,t+r.h);
  ctx.lineTo(l,t+r.h-c);ctx.lineTo(l,t+c);ctx.closePath();
}
function fogged(r,seen=new Set()){
  if(!r.item||seen.has(r.id)||opened.has(r.id))return false;
  seen.add(r.id);return locked(r)||array(r.item.needs).some(id=>{const parent=roomById.get(id);return parent&&fogged(parent,seen);});
}
function glow(x,y,radius,strength){
  const light=ctx.createRadialGradient(x,y,0,x,y,radius);light.addColorStop(0,`rgba(242,177,52,${strength})`);light.addColorStop(1,'rgba(242,177,52,0)');
  ctx.fillStyle=light;ctx.fillRect(x-radius,y-radius,radius*2,radius*2);
}
function draw(now=performance.now()){
  frame=0;const dpr=devicePixelRatio||1;ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle=colors.ink;ctx.fillRect(0,0,width,height);
  ctx.save();ctx.translate(width/2-camera.x*camera.z,height/2-camera.y*camera.z);ctx.scale(camera.z,camera.z);
  // Architectural regions are the type layout; only measured visits supply light.
  for(const wing of wings){
    const u={x:Math.cos(wing.angle),y:Math.sin(wing.angle)},v={x:-u.y,y:u.x};
    const at=(r,t)=>({x:u.x*(r+wing.offset)+v.x*t,y:u.y*(r+wing.offset)+v.y*t});
    const outline=[[198,-55],[235,-120],[wing.extent,-145],[wing.extent+45,-70],[wing.extent+45,70],[wing.extent,145],[235,120],[198,55]];
    ctx.beginPath();outline.forEach(([r,t],i)=>{const p=at(r,t);if(i)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y);});ctx.closePath();
    ctx.fillStyle='#100d0840';ctx.fill();ctx.strokeStyle='#6e542d55';ctx.lineWidth=.8/camera.z;ctx.stroke();
    const mouth=at(200,0),end=at(wing.extent,0);ctx.beginPath();ctx.moveTo(mouth.x,mouth.y);ctx.lineTo(end.x,end.y);ctx.strokeStyle='#b38b4038';ctx.lineWidth=.7;ctx.stroke();
  }
  ctx.beginPath();ctx.arc(0,0,184,0,Math.PI*2);ctx.strokeStyle='#6e542d70';ctx.lineWidth=.9;ctx.stroke();
  for(const e of edges){
    const a=roomById.get(e.from),b=roomById.get(e.to);if(!a||!b)continue;
    const shut=e.kind==='needs'&&fogged(b),lit=(a.kind==='hub'||visited(a.item||{})||visited(b.item||{}))&&!shut;
    const dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy)||1,ux=dx/length,uy=dy/length;
    const trimA=Math.min(a.w/(2*Math.max(.01,Math.abs(ux))),a.h/(2*Math.max(.01,Math.abs(uy)))),trimB=Math.min(b.w/(2*Math.max(.01,Math.abs(ux))),b.h/(2*Math.max(.01,Math.abs(uy))));
    let start={x:a.x+ux*trimA,y:a.y+uy*trimA};const end={x:b.x-ux*trimB,y:b.y-uy*trimB};
    if(a.kind==='wing'&&e.kind==='passage'){const u={x:Math.cos(a.angle),y:Math.sin(a.angle)},distance=(end.x-a.x)*u.x+(end.y-a.y)*u.y;start={x:a.x+u.x*distance,y:a.y+u.y*distance};}
    const door={x:start.x+(end.x-start.x)*.72,y:start.y+(end.y-start.y)*.72};
    ctx.strokeStyle=lit?'#cf963e90':'#a87c363c';ctx.lineWidth=(e.kind==='needs'?1.1:.65)/Math.max(.6,camera.z);ctx.shadowColor='#f2b13444';ctx.shadowBlur=lit?5:0;
    ctx.beginPath();ctx.moveTo(start.x,start.y);if(shut){ctx.lineTo(door.x-ux*7,door.y-uy*7);ctx.moveTo(door.x+ux*7,door.y+uy*7);}ctx.lineTo(end.x,end.y);ctx.stroke();ctx.shadowBlur=0;
    if(shut){ctx.save();ctx.translate(door.x,door.y);ctx.rotate(Math.atan2(dy,dx));ctx.fillStyle=colors.ink;ctx.fillRect(-5,-10,10,20);ctx.strokeStyle='#d4a357';ctx.lineWidth=1;ctx.strokeRect(-3,-9,6,18);ctx.beginPath();ctx.moveTo(-3,0);ctx.lineTo(3,0);ctx.stroke();ctx.restore();}
  }
  footprints.forEach((b,i)=>{const p=roomPoint(placeOf(b));if(!p)return;ctx.globalAlpha=(i+1)/footprints.length*.38;ctx.fillStyle=colors.amber;ctx.beginPath();ctx.arc(p.x-24+i*4,p.y+23,1.2,0,Math.PI*2);ctx.fill();});ctx.globalAlpha=1;
  const actorRoom=state.run&&state.shuttle?.state!=='released'?roomById.get(actor):null;
  for(const r of rooms){
    const sx=(r.x-camera.x)*camera.z+width/2,sy=(r.y-camera.y)*camera.z+height/2;
    if(sx+r.w*camera.z<0||sx-r.w*camera.z>width||sy+r.h*camera.z<0||sy-r.h*camera.z>height)continue;
    const hand=r.id===selected,shut=fogged(r),fresh=visited(r.item||{}),resident=r.id===actorRoom?.id;
    const hubLit=r.kind==='hub'&&(resident||array(state.beads).some(b=>placeOf(b)===r.id));
    const lit=(fresh||hubLit)&&!shut,done=r.item?.state==='done';
    const left=r.x-r.w/2,top=r.y-r.h/2;
    if(r.kind==='wing'){
      const size=Math.max(13,10/camera.z);drawText(r.title,r.x-r.w/2,r.y-19,colors.text,size,Math.max(140,100/camera.z));continue;
    }
    if(lit||resident)glow(r.x,r.y,Math.max(r.w,r.h)*.95,resident?.18:.08);
    ctx.fillStyle=colors.ink;chamber(r);ctx.fill();
    ctx.globalAlpha=shut?.18:lit?.9:r.kind==='hub'?.52:.25;
    ctx.strokeStyle=colors.amber;ctx.lineWidth=lit?1.15:.8;ctx.shadowColor='#f2b13477';ctx.shadowBlur=lit?9:0;chamber(r);ctx.stroke();ctx.shadowBlur=0;ctx.globalAlpha=1;
    if(hand){ctx.strokeStyle=colors.ice;ctx.lineWidth=1.3/camera.z;chamber({...r,w:r.w+7,h:r.h+7});ctx.stroke();}
    if(camera.z<.32&&r.kind!=='hub')continue;
    if(r.kind==='reference'){drawText(r.title,left+8,r.y+4,colors.dim,Math.max(9,8/camera.z),r.w-16);continue;}
    if(r.kind==='strand'){
      if(camera.z<.7){drawText('▱',r.x-4,r.y+4,colors.amber,14);continue;}
      drawText('▱ '+r.title,left+7,top+15,colors.amber,8,r.w-14);
      const bucket=array(state.fuel?.buckets).find(b=>b.name===r.strand.bucket),window=array(bucket?.windows).find(w=>w.binding)||array(bucket?.windows)[0];
      if(window&&number(window.pct_left)){ctx.fillStyle='#3a3328';ctx.fillRect(left+7,top+26,r.w-14,2);ctx.fillStyle=colors.amber;ctx.fillRect(left+7,top+26,(r.w-14)*Math.max(0,Math.min(100,window.pct_left))/100,2);drawText(`${window.name} ${window.pct_left}%`,left+7,top+39,colors.dim,7,r.w-14);}else drawText('fuel ?',left+7,top+34,colors.dim,8);
      continue;
    }
    if(r.kind==='hub'){
      const tower=r.id==='shed'&&state.shuttle?.state==='listening';
      if(tower){ctx.strokeStyle=colors.amber;ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(left+9,top);ctx.lineTo(left+9,top-14);ctx.lineTo(left+15,top-14);ctx.lineTo(left+15,top-7);ctx.lineTo(left+21,top-7);ctx.lineTo(left+21,top-14);ctx.lineTo(left+27,top-14);ctx.lineTo(left+27,top);ctx.stroke();}
      drawText(r.title,left+8,r.y+(resident?14:4),lit?colors.text:colors.dim,Math.max(11,9/camera.z),r.w-16);
      if(camera.z>=1.1&&!resident)drawText(hubDetail(r.id),left+8,top+r.h+13,colors.dim,7,r.w+10);
      continue;
    }
    const item=r.item,his=item.type==='decision'&&item.state==='ready';
    if(shut){drawText('▣ '+r.id,left+8,r.y+4,'#826434',Math.max(9,8/camera.z),r.w-16);continue;}
    ctx.globalAlpha=hand?1:lit?.95:.35;
    const textColor=hand?colors.ice:colors.text;
    drawText(`${symbols[item.type]||'·'} ${r.id}${his?' · his':''}`,left+8,top+17,textColor,Math.max(9,8/camera.z),r.w-16);
    if(camera.z>=.72){wrap(item.title,left+8,top+33,r.w-16,item.type==='decision'||item.type==='goal'?2:1,textColor,9);}
    if(item.type==='goal'&&camera.z>=.72)wrap(item.metric||'metric unmeasured',left+8,top+76,r.w-16,1,colors.dim,8);
    if(done&&item.receipt&&camera.z>=.72)drawText(receiptText(item.receipt),left+8,top+r.h-7,colors.dim,8,r.w-16);
    ctx.globalAlpha=1;
    if(his){const downstream=opens(r);drawText(`opens ${downstream.join(', ')||'—'}`,left+4,top+r.h+14,hand?colors.ice:'#be9955',Math.max(9,8/camera.z),Math.max(r.w,90/camera.z));if(camera.z>=.85)drawText(`stake ${item.stake??'?'}`,left+4,top+r.h+27,colors.dim,8,r.w+40);}
    if(item.taken&&item.taken!==state.run?.id)drawText('▱',r.x+r.w/2-10,r.y-4,colors.amber,14);
  }
  if(actorRoom){const p=actorPoint||actorRoom;glow(p.x,p.y,42,.12);ctx.shadowColor='#f2b13499';ctx.shadowBlur=10;drawText(state.run?.mood_glyph||'b·_·d',p.x-24,p.y-5,colors.amber,Math.max(14,11/camera.z));ctx.shadowBlur=0;}
  if(pulse){const p=roomById.get(pulse.id),t=(now-pulse.at)/650;if(p&&t<1){ctx.strokeStyle=`rgba(242,177,52,${(1-t)*.65})`;ctx.lineWidth=2;chamber({...p,w:p.w+8*t,h:p.h+8*t});ctx.stroke();requestDraw();}else pulse=null;}
  ctx.restore();
  // Static glass treatment: never a sweep, clock, pulse or invented activity.
  const vignette=ctx.createRadialGradient(width/2,height/2,Math.min(width,height)*.18,width/2,height/2,Math.max(width,height)*.7);vignette.addColorStop(0,'#0000');vignette.addColorStop(1,'#0008');ctx.fillStyle=vignette;ctx.fillRect(0,0,width,height);
  ctx.fillStyle='#f2b13404';for(let y=0;y<height;y+=4)ctx.fillRect(0,y,width,1);
}
function requestDraw(){if(!frame)frame=requestAnimationFrame(draw);}
function resize(){const b=canvas.getBoundingClientRect();width=b.width;height=b.height;canvas.width=Math.round(width*(devicePixelRatio||1));canvas.height=Math.round(height*(devicePixelRatio||1));requestDraw();}
function center(id,z){const p=roomPoint(id);if(!p)return;camera.x=p.x;camera.y=p.y;if(z)camera.z=z;requestDraw();}
function choose(id,pan=true){if(!roomById.has(id))return;selected=id;if(pan)center(id);selection();requestDraw();}
function selection(){
  const r=roomById.get(selected);if(!r)return;
  const box=$('selection');box.replaceChildren();
  const b=element('button',locked(r)?'Reveal door':r.kind==='wing'?'Enter wing':'Open page');b.onclick=()=>activate(r);box.append(b);
  box.append(element('strong',`${symbols[r.item?.type]||'◇'} ${r.id}${r.kind==='item'&&!locked(r)?` · ${r.title}`:''}`));
  const info=r.item?`${ownership(r.item)} · ${r.item.state||'?'} · ${opens(r).length?`opens ${opens(r).join(', ')}`:'no downstream rooms'} · stake ${r.item.stake??'?'}`:r.kind==='hub'?hubDetail(r.id):r.title;
  box.append(element('div',info,'door-info'));
  if(r.item?.needs?.length)box.append(element('div',`${locked(r)?'Locked':'Needs'}: ${r.item.needs.join(', ')}${opened.has(r.id)?' · revealed by your hand; work state unchanged':''}`,'muted'));
  $('map-caption').textContent='THE WARP / LIGHT IS A VISIT';
}
function receiptText(receipt){if(typeof receipt==='string')return receipt;return [receipt.kind,receipt.number!=null?`#${receipt.number}`:null,receipt.action,receipt.commit?.slice(0,8)].filter(Boolean).join(' ')||'receipt recorded';}
function renderPanels(){
  const shuttle=state.shuttle||{},last=array(state.beads).at(-1), place=shuttle.state==='listening'?'shed':shuttle.place||placeOf(last)||'shed';
  $('glyph').textContent=state.run?.mood_glyph||'b·_·d';$('glyph').title=`Locate resident: ${place}`;$('state').textContent=shuttle.state||'unmeasured';
  $('act').textContent=shuttle.state==='listening'?'listening · shed → wire':last?`${last.act||'act'} · ${place}`:state.run?`at ${place} · no act reading`:'No resident reading';
  const since=shuttle.since||state.run?.started;
  $('since').textContent=since&&Number.isFinite(timestamp(since))?`since ${new Date(timestamp(since)).toISOString().slice(11,16)} UTC`:'';
  $('waiting').textContent=shuttle.waiting_on?`waiting on ${shuttle.waiting_on}`:shuttle.state==='held'?`held · ${shuttle.why||'reason unmeasured'}`:'';
  $('fuel').replaceChildren();
  const buckets=[...array(state.fuel?.buckets)].sort((a,b)=>Number(!!b.seat)-Number(!!a.seat));
  if(!buckets.length)$('fuel').append(element('p','No fuel reading','muted'));
  for(const bucket of buckets){
    const block=element('div',null,`bucket${bucket.seat?'':' secondary'}`),name=element('div',bucket.name,'bucket-name');name.append(element('span',bucket.seat?'SEAT':'BUCKET'));block.append(name);
    for(const window of array(bucket.windows)){
      const row=element('div',null,`window${window.binding?' binding':''}`),heading=element('div',null,'window-heading'),value=element('strong',number(window.pct_left)?String(Math.round(window.pct_left)):'?');value.append(element('small',number(window.pct_left)?'%':''));heading.append(element('span',window.name||'window'),value);row.append(heading);
      if(number(window.pct_left)){const bar=element('div',null,'bar'),fill=element('i');fill.style.width=`${Math.min(100,Math.max(0,window.pct_left))}%`;bar.append(fill);row.append(bar);}
      const reset=timestamp(window.resets_at), seconds=Number.isFinite(reset)?(reset-referenceNow())/1000:null;
      row.append(element('div',seconds===null?'↻ reset unmeasured':seconds<=0?'↻ reset due · awaiting reading':`↻ resets in ${duration(seconds)}`,'reset'));
      const f=window.forecast;
      if(number(f?.rate_pct_per_h)&&number(f?.dry_in_s)&&f.dry_in_s>=0){
        let forecast=null;
        if(f.dries_first===true&&number(f.resets_in_s))forecast=`runs dry ${duration(Math.max(0,f.resets_in_s-f.dry_in_s))} before the reset`;
        else if(f.dries_first===false&&number(f.pct_left_at_reset))forecast=`lasts to the reset (+${Math.max(0,Math.round(f.pct_left_at_reset))}% left)`;
        if(forecast)row.append(element('div',forecast,'forecast'));
      }
      block.append(row);
    }
    $('fuel').append(block);
  }
  // A seat never inherits the legacy HUD's allowance field.
  if(state.hud?.spend && (state.run?.source==='spawn'||state.run?.parent||state.hud?.full?.run?.source==='spawn')){
    const spend=state.hud.spend;$('fuel').append(element('p',`Allowance ${compact(spend.tokens)} / ${compact(spend.allowance_tokens)}`,'muted'));
  }
  renderPack($('inventory'),false);
}
function renderPack(target,all){
  target.replaceChildren();const pack=state.pack;
  if(!pack){target.append(element('p','No pack reading','muted'));return;}
  target.append(element('p',`${compact(pack.ctx_tokens)} / ${compact(pack.window_tokens)} tokens`));
  const blocks=array(pack.blocks).map(b=>({...b,bytes_kept:b.bytes_kept??b.bytes})).sort((a,b)=>(b.bytes_kept??-1)-(a.bytes_kept??-1));
  if(blocks.length&&blocks.every(b=>number(b.bytes_kept)))target.append(element('p',`${blocks.length} blocks · ${bytes(blocks.reduce((n,b)=>n+b.bytes_kept,0))} kept`,'muted'));
  for(const block of (all?blocks:blocks.slice(0,3))){const row=element('div',null,'slot');row.append(element('span',block.label||block.name),element('span',bytes(block.bytes_kept)));target.append(row);}
}
function routeFor(room){
  if(room.kind==='item')return `/loom/page/item?id=${encodeURIComponent(room.id)}`;
  if(room.kind==='wing')return `/loom/page/heddle?slug=${encodeURIComponent(room.slug)}`;
  if(room.id==='shed'&&state.run?.id)return `/loom/page/pass?id=${encodeURIComponent(state.run.id)}`;
  return null;
}
async function activate(room){
  if(!room)return;
  if(room.kind==='strand'){await fetchPage(`/loom/page/pass?id=${encodeURIComponent(room.strand.id)}`,room.title);return;}
  if(room.kind==='reference'){choose(room.target);return;}
  if(locked(room)){opened.add(room.id);selection();requestDraw();return;}
  if(room.kind==='wing'){const child=rooms.find(r=>r.wing===room.slug);if(child)choose(child.id);return;}
  if(room.id==='archive'){openBench({kind:'archive',title:'The archive'});return;}
  if(room.id==='pack'){openBench({kind:'pack',title:'The pack'});return;}
  if(room.id==='belt'){openBench({kind:'belt',title:'The belt'});return;}
  const route=routeFor(room);
  if(route)await fetchPage(route,room.title);
  else openBench({kind:'hub',title:room.title,id:room.id});
}
function openBench(page,push=true){if(push)history.push(page);$('bench').hidden=false;renderPage(page);$('page').tabIndex=-1;$('page').focus({preventScroll:true});}
async function fetchPage(url,title){
  const generation=++pageGeneration;openBench({kind:'loading',title});
  try{const response=await fetch(url);const data=await response.json();if(generation!==pageGeneration)return;
    const page={...data,kind:url.includes('/place?')?'file':url.includes('/item?')?'item':'pass',title:data.title||title};
    if(!response.ok)Object.assign(page,{kind:'error',body:data.error||`Page unavailable (${response.status})`});
    history[history.length-1]=page;renderPage(page);
  }catch(error){if(generation===pageGeneration){const page={kind:'error',title,body:`Page unavailable: ${error.message}`};history[history.length-1]=page;renderPage(page);}}
}
function renderPage(page){
  const target=$('page');target.replaceChildren(element('h1',page.title||page.path||'Page'));
  if(page.kind==='readings'){ target.append(element('p','Every field in this reading. Null means unmeasured.','muted')); appendReading(target,state); return; }
  if(page.kind==='loading'){target.append(element('p','Opening page…','muted'));return;}
  if(page.kind==='archive'){renderArchive();return;}
  if(page.kind==='pack'){const body=element('div');renderPack(body,true);target.append(body);return;}
  if(page.kind==='belt'){target.append($('fuel').cloneNode(true));target.lastChild.removeAttribute('id');return;}
  if(page.kind==='hub'){renderHub(page.id,target);return;}
  if(page.kind==='file'){
    const text=typeof page.text==='string'?page.text:page.text?.text;
    const ranges=array(page.attention),pre=element('pre',null,'file-lines');
    if(text!=null)String(text).split('\n').forEach((line,i)=>{const n=(page.text?.from||1)+i,marks=ranges.filter(r=>n>=r.from&&n<=r.to),span=element('span',`${String(n).padStart(4)}  ${line}`,`ln${marks.some(r=>r.kind==='edit')?' edit':marks.length?' read':''}`);pre.append(span);});
    else pre.textContent=page.text?.binary?'Binary file — text unavailable.':'No file text returned.';
    target.append(element('p','Amber: read ranges · hatch: edit ranges','muted'));
    if(page.text&&typeof page.text==='object'&&number(page.text.total)){
      target.append(element('p',`Lines ${page.text.from??'?'}–${page.text.to??'?'} of ${page.text.total}`,'muted'));
      if(page.text.to<page.text.total){const next=element('button','Read next 200 lines');next.onclick=()=>fetchPage(`/loom/page/place?path=${encodeURIComponent(page.path)}&from=${page.text.to+1}&to=${Math.min(page.text.total,page.text.to+200)}`,page.title);target.append(next);}
    }
    target.append(pre);
    if(page.gh_url){try{const url=new URL(page.gh_url);if(url.protocol==='https:'){const link=element('a','Open source');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';target.append(link);}}catch{}}
    return;
  }
  if(page.kind==='item')target.append(element('p',`${page.type||'item'} · ${page.state||'?'}${page.needs?.length?` · needs ${page.needs.join(', ')}`:''}`,'muted'));
  const body=page.body||page.prompt||page.contract||page.card?.now;
  if(body)target.append(element('pre',body));
  else target.append(element('p','This page has no prose body.','muted'));
  if(page.metric){target.append(element('h2','Metric'),element('p',page.metric));}
  if(page.produce){target.append(element('h2','Produce'));for(const pr of array(page.produce.prs))target.append(element('p',`PR #${pr.number} · ${pr.state||''}`));}
  if(page.kind==='pass'&&page.card?.plan?.length){target.append(element('h2','Course'));for(const row of page.card.plan)target.append(element('p',`${row.done?'✓':'○'} ${row.text}`));}
}
function appendReading(target,value){
  for(const [key,child] of Object.entries(value||{})){
    if(child!==null&&typeof child==='object'){const d=element('details'),summary=element('summary',`${key} · ${Array.isArray(child)?child.length+' entries':Object.keys(child).length+' fields'}`);d.append(summary);d.addEventListener('toggle',()=>{if(d.open&&!d.dataset.loaded){d.dataset.loaded='true';appendReading(d,child);}});target.append(d);}
    else {const p=element('p');p.append(element('span',key+': ','muted'),document.createTextNode(child===null?'unmeasured':String(child)));target.append(p);}
  }
}
function renderHub(id,target){
  target.append(element('p',hubDetail(id)));
  if(id==='clock'&&state.hud?.full?.schedule){appendReading(target,state.hud.full.schedule);}
  if(id==='crew')for(const strand of array(state.hud?.strands)){
    target.append(element('h2',strand.title||strand.id),element('p',`${strand.status||'?'} · ${strand.place||strand.last_place_kind||'place unmeasured'}`));
    const bucket=array(state.fuel?.buckets).find(b=>b.name===strand.bucket);
    if(bucket)for(const w of array(bucket.windows))target.append(element('p',`${w.name}: ${number(w.pct_left)?w.pct_left+'%':'?'} · ↻ ${Number.isFinite(timestamp(w.resets_at))?duration((timestamp(w.resets_at)-referenceNow())/1000):'unmeasured'}`));
    else target.append(element('p','Bucket not measured for this strand.','muted'));
    const button=element('button','Open strand page');button.onclick=()=>fetchPage(`/loom/page/pass?id=${encodeURIComponent(strand.id)}`,strand.title||strand.id);target.append(button);
  }
  for(const relic of array(state.relics).filter(r=>r.place===id))target.append(element('p',receiptText(relic)));
  const bead=[...array(state.beads)].reverse().find(b=>placeOf(b)===id);
  if(bead){target.append(element('h2','Last measured act'),element('p',`${bead.act||'act'} · ${bead.at||''}`));}
}
function archivePlaces(){
  const tree=state.tree||{};
  return [...array(tree.repo||tree.places).map(p=>({...p,root:'repo'})),...array(tree.home?.places).map(p=>({...p,root:'home'}))].filter(p=>p.knots>=1&&!String(p.path).split('/').includes('node_modules'));
}
function renderArchive(){
  const root={name:'',children:new Map()},files=archivePlaces();
  for(const p of files){let node=root;for(const part of [p.root,...p.path.split('/')]){if(!node.children.has(part))node.children.set(part,{name:part,children:new Map()});node=node.children.get(part);}node.place=p;}
  archiveRows=[];
  function walk(node,path,depth){for(const child of [...node.children.values()].sort((a,b)=>collator.compare(a.name,b.name))){const key=path?`${path}/${child.name}`:child.name;archiveRows.push({node:child,key,depth});if(archiveExpanded.has(key))walk(child,key,depth+1);}}
  walk(root,'',0);archiveIndex=Math.max(0,Math.min(archiveIndex,archiveRows.length-1));
  const target=$('page');target.replaceChildren(element('h1','The archive'),element('p','j / k walk · l expand · h collapse · enter read · esc back','muted'));
  if(!files.length)target.append(element('p','No knotted places in this reading.','muted'));
  archiveRows.forEach((row,i)=>{const hasChildren=row.node.children.size>0,b=element('button',`${hasChildren?(archiveExpanded.has(row.key)?'⌄':'›'):'·'} ${row.node.name}${row.node.place?`  ${row.node.place.knots} knots`:''}`,`tree-row${i===archiveIndex?' active':''}${!row.node.place?.heat?' dim':''}`);b.style.setProperty('--depth',row.depth);b.onclick=()=>{archiveIndex=i;archiveAct('Enter');};b.onfocus=()=>{archiveIndex=i;};target.append(b);});
}
function archiveAct(key){const row=archiveRows[archiveIndex];if(!row)return;
  if(key==='j'||key==='ArrowDown')archiveIndex=Math.min(archiveRows.length-1,archiveIndex+1);
  else if(key==='k'||key==='ArrowUp')archiveIndex=Math.max(0,archiveIndex-1);
  else if(key==='l'||key==='ArrowRight')archiveExpanded.add(row.key);
  else if(key==='h'||key==='ArrowLeft'){if(archiveExpanded.has(row.key))archiveExpanded.delete(row.key);else {const parent=row.key.split('/').slice(0,-1).join('/');const i=archiveRows.findIndex(r=>r.key===parent);if(i>=0)archiveIndex=i;}}
  else if(key==='Enter'){if(row.node.place){const path=row.node.place.path;fetchPage(`/loom/page/place?path=${encodeURIComponent(path)}`,row.node.place.path);return;}if(archiveExpanded.has(row.key))archiveExpanded.delete(row.key);else archiveExpanded.add(row.key);}
  renderArchive();$('page').querySelector('.tree-row.active')?.scrollIntoView({block:'nearest'});
}
function back(){pageGeneration++;if(history.length>1){history.pop();renderPage(history.at(-1));}else closeBench();}
function closeBench(){pageGeneration++;history=[];$('bench').hidden=true;canvas.focus();}
function move(key){
  const r=roomById.get(selected);if(!r)return;
  const vector={h:[-1,0],ArrowLeft:[-1,0],l:[1,0],ArrowRight:[1,0],k:[0,-1],ArrowUp:[0,-1],j:[0,1],ArrowDown:[0,1]}[key];if(!vector)return;
  const linked=new Set(edges.filter(e=>e.from===r.id||e.to===r.id).map(e=>e.from===r.id?e.to:e.from));
  const candidates=rooms.filter(n=>linked.has(n.id)).map(n=>{const dx=n.x-r.x,dy=n.y-r.y,dot=dx*vector[0]+dy*vector[1];return {n,dot,score:Math.hypot(dx,dy)+Math.abs(dx*vector[1]-dy*vector[0])*2};}).filter(n=>n.dot>0).sort((a,b)=>a.score-b.score);
  if(candidates[0])choose(candidates[0].n.id);
}
function stepActor(target){
  clearTimeout(stepTimer);actorPoint=null;
  const queue=[[actor]],seen=new Set([actor]);let path=null;
  while(queue.length){const candidate=queue.shift(),last=candidate.at(-1);if(last===target){path=candidate;break;}
    for(const e of edges){const next=e.from===last?e.to:e.to===last?e.from:null;if(next&&!seen.has(next)){seen.add(next);queue.push([...candidate,next]);}}
  }
  if(!path||matchMedia('(prefers-reduced-motion: reduce)').matches){actor=target;pulse={id:target,at:performance.now()};requestDraw();return;}
  const steps=path.slice(1);
  function step(){actor=steps.shift()||target;canvas.dataset.actor=actor;requestDraw();if(steps.length)stepTimer=setTimeout(step,180);else{pulse={id:target,at:performance.now()};requestDraw();}}
  step();
}
function updateActor(first){
  const beads=array(state.beads),newest=beads.at(-1),key=newest?`${state.run?.id}:${newest.n}:${newest.at}`:null;
  const target=state.shuttle?.state==='listening'?'shed':state.shuttle?.place||placeOf(newest)||'shed';
  footprints=beads.slice(-12);
  if(first){actor=target;lastBead=key;return;}
  if(state.shuttle?.state==='listening'){clearTimeout(stepTimer);actor='shed';actorPoint=null;}
  else if(key&&key!==lastBead&&target!==actor)stepActor(target);
  lastBead=key;
}
function ingest(next){state=next;const first=!initialized;if(first)resize();if(first)loadLayout();buildMap();updateActor(first);renderPanels();selection();
  if(first){initialized=true;const door=rooms.find(r=>r.item?.type==='decision'&&r.item.state==='ready');choose(door?.id||'shed',false);camera={x:0,y:0,z:Math.min(1.25,(width-20)/670,(height-26)/420)};selection();}
  $('source').textContent=fixture?`${fixture.toUpperCase()} FIXTURE · frozen`:`${state.repo||'local'} · live`;
  canvas.dataset.ready='true';canvas.dataset.actor=actor;canvas.dataset.rooms=String(rooms.length);requestDraw();
}
let drag=null;
canvas.addEventListener('pointerdown',event=>{canvas.focus();canvas.setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY,cx:camera.x,cy:camera.y,moved:false};});
canvas.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>4)drag.moved=true;camera.x=drag.cx-dx/camera.z;camera.y=drag.cy-dy/camera.z;requestDraw();});
canvas.addEventListener('pointerup',event=>{if(!drag)return;const moved=drag.moved;drag=null;if(moved)return;const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left-width/2)/camera.z+camera.x,y=(event.clientY-rect.top-height/2)/camera.z+camera.y;const r=[...rooms].reverse().find(r=>Math.abs(x-r.x)<=r.w/2&&Math.abs(y-r.y)<=r.h/2);if(r){const again=selected===r.id;choose(r.id,false);if(again)activate(r);}});
canvas.addEventListener('pointercancel',()=>{drag=null;});
canvas.addEventListener('wheel',event=>{event.preventDefault();if(event.ctrlKey||event.metaKey){camera.z=Math.max(.12,Math.min(1.7,camera.z*(event.deltaY>0?.9:1.1)));}else{camera.x+=event.deltaX/camera.z;camera.y+=event.deltaY/camera.z;}requestDraw();},{passive:false});
canvas.addEventListener('mousemove',event=>{const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left-width/2)/camera.z+camera.x,y=(event.clientY-rect.top-height/2)/camera.z+camera.y;const r=rooms.find(r=>Math.abs(x-r.x)<=r.w/2&&Math.abs(y-r.y)<=r.h/2);canvas.title=r?.item?`${r.id} · ${ownership(r.item)} · opens ${opens(r).join(', ')||'—'} · stake ${r.item.stake??'?'}`:r?.title||'';});
document.addEventListener('keydown',event=>{
  if(event.key==='?'&&!['INPUT','TEXTAREA'].includes(event.target.tagName)){openBench({kind:'readings',title:'All measured readings'});event.preventDefault();return;}
  if(event.key==='Escape'){if(!$('bench').hidden)back();else choose('shed');event.preventDefault();return;}
  if(['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName))return;
  if(!$('bench').hidden){if(history.at(-1)?.kind==='archive'&&['j','k','l','h','ArrowDown','ArrowUp','ArrowRight','ArrowLeft','Enter'].includes(event.key)){event.preventDefault();archiveAct(event.key);}return;}
  if(event.target.tagName==='BUTTON')return;
  if(['h','j','k','l','ArrowDown','ArrowUp','ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();move(event.key);}
  if(event.key==='Enter'){event.preventDefault();activate(roomById.get(selected));}
});
$('glyph').onclick=()=>{if(roomById.has(actor)){choose(actor);camera.z=width<600?.92:1;requestDraw();}};
$('hub').onclick=()=>{choose('shed',false);camera.x=0;camera.y=0;camera.z=Math.min(1.25,(width-20)/670,(height-26)/420);requestDraw();};
$('doors').onclick=()=>{const doors=rooms.filter(r=>r.item?.type==='decision'&&r.item.state==='ready');if(doors.length){const next=doors[(doors.findIndex(r=>r.id===selected)+1)%doors.length];choose(next.id);camera.z=width<600?.92:1;requestDraw();}else{$('selection').replaceChildren(element('span','No ready decisions in this reading.'));}};
$('wing').onchange=event=>{if(event.target.value){choose(event.target.value);camera.z=.85;requestDraw();}canvas.focus();};
$('zoom-in').onclick=()=>{camera.z=Math.min(1.7,camera.z*1.25);requestDraw();};$('zoom-out').onclick=()=>{camera.z=Math.max(.12,camera.z/1.25);requestDraw();};
$('overview').onclick=()=>{$('map-caption').textContent='THE MAP / SELECT A WING TO ENTER';const xs=rooms.map(r=>r.x),ys=rooms.map(r=>r.y),minX=Math.min(...xs)-180,maxX=Math.max(...xs)+180,minY=Math.min(...ys)-120,maxY=Math.max(...ys)+120;camera={x:(minX+maxX)/2,y:(minY+maxY)/2,z:Math.min(1,(width-30)/(maxX-minX),(height-30)/(maxY-minY))};requestDraw();};
$('readings').onclick=()=>openBench({kind:'readings',title:'All measured readings'});
$('back').onclick=back;$('close').onclick=closeBench;
new ResizeObserver(resize).observe($('viewport'));
async function load(){
  try{if(fixture&&!fixtures.has(fixture))throw Error('Unknown fixture');const response=await fetch(fixture?`/loom/dev/fixtures/${fixture}.json`:'/loom/state.json',{cache:'no-store'});if(!response.ok)throw Error(`feed ${response.status}`);ingest(await response.json());}
  catch(error){$('source').textContent=`unavailable · ${error.message}`;if(!initialized){ingest({});$('source').textContent=`unavailable · ${error.message}`;}}
}
await load();
if(!fixture)setInterval(load,10000);
