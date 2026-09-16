/* The dungeon. The feed owns facts; the hand owns camera, selection and doors.
 * Canvas is redrawn on input or a reading. Animation runs only for a new bead.
 */
const $ = id => document.getElementById(id);
const canvas = $('map'), ctx = canvas.getContext('2d');
const fixture = new URLSearchParams(location.search).get('fixture');
const fixtures = new Set(['live', 'empty', 'three', 'eighty']);
const colors = { ink:'#0b0906', line:'#3a3328', text:'#e8dcc0', dim:'#b3a78e', ice:'#8fd3ff', amber:'#f2b134',
  absent:'#2b2318', absentText:'#6d5f4b', ember:'#7a5c27' };
/* The color law (§9): colour is the temperature of a reading.
 * amber = warm, alive, spending, the resident's trace · ice = a reading gone low, and the user's hand
 * black = absent, paused, unknown — never a grey placeholder, never dim amber for "nobody was here".
 */
const channels = hex => [1,3,5].map(i => parseInt(hex.slice(i,i+2),16));
const mix = (cold,warm,t) => { const a=channels(cold),b=channels(warm),k=Math.max(0,Math.min(1,t));
  return `rgb(${a.map((v,i)=>Math.round(v+(b[i]-v)*k)).join(',')})`; };
const WARM_PCT = 48, COLD_PCT = 18;
const ease = t => t*t*(3-2*t);
// null = unmeasured. A measured reading is 1 at full and 0 once it is spent; the knee is sharp
// so a window reads warm or reads cold — the blend between them is a band, not a colour to sit in.
const temperature = pct => typeof pct === 'number' && Number.isFinite(pct)
  ? ease(ease(Math.max(0,Math.min(1,(pct-COLD_PCT)/(WARM_PCT-COLD_PCT))))) : null;
const tempColor = pct => { const t=temperature(pct); return t===null?colors.absent:mix(colors.ice,colors.amber,t); };
const bindingWindow = name => { const bucket=(Array.isArray(state.fuel?.buckets)?state.fuel.buckets:[]).find(b=>b.name===name);
  const windows=Array.isArray(bucket?.windows)?bucket.windows:[];return windows.find(w=>w.binding)||windows[0]||null; };
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
let state = {}, rooms = [], roomById = new Map(), edges = [], wings = [], rings = [], selected = 'shed';
let opened = new Set(), camera = {x:0,y:0,z:1}, width = 1, height = 1, actor = 'shed', actorPoint = null;
let stepTimer = null;
let lastBead = null, pulse = null, frame = 0, footprints = [], history = [], pageGeneration = 0, archiveIndex = 0;
let archiveExpanded = new Set(['repo','home']), archiveRows = [], initialized = false;
const referenceNow = () => fixture ? timestamp(state.at) : Date.now();
const visited = item => Number.isFinite(timestamp(item.visited_at)) && referenceNow() - timestamp(item.visited_at) <= 7*86400000;
/* The generative rule (§9): depth is dependency, angle is topic, radius grows with count.
 * Every distance on this map is a fraction of ring 1's radius, so the first view of three rooms
 * and of eighty is the same picture at the same scale — only the ring's population differs.
 */
const FOOT = {goal:196,decision:170,action:92,preparation:80,reference:62};
const BOX  = {goal:[176,90],decision:[150,74],action:[78,46],preparation:[68,40],reference:[52,28]};
const RING1_MIN = 430, RING_GAP = 168, GUTTER = 20;
const HUB_FRACTION = .30, SECTOR_FLOOR = .055;
// A room's ring is its dependency depth. Two needs ⇒ the deepest chain: the room is entered
// only after all of them, so the longest path is its honest distance from the hub.
function depthOf(item,byId,seen=new Set()){
  if(!item||seen.has(item.id))return 1;
  seen.add(item.id);
  const parents=array(item.needs).map(id=>byId.get(id)).filter(Boolean);
  return parents.length?1+Math.max(...parents.map(p=>depthOf(p,byId,new Set(seen)))):1;
}
function frameFirstView(){
  const work=rooms.some(r=>r.kind==='item'||r.kind==='reference');
  const edge=work?(rings[1]||RING1_MIN)*1.14:rings[0]*2.4;   // nothing built yet ⇒ frame the hub itself
  camera={x:0,y:0,z:Math.min((width-18)/(2*edge),(height-18)/(2*edge))};
  canvas.dataset.ring1=String(Math.round(rings[1]||0));canvas.dataset.z=camera.z.toFixed(3);
  window.__loomFrame={z:camera.z,ring1:rings[1]||0,rings:rings.map(r=>Math.round(r))};
  requestDraw();
}
function buildMap() {
  rooms = []; edges = []; wings = []; rings = [RING1_MIN*HUB_FRACTION];
  const add = r => { rooms.push(r); return r; };
  const items=array(state.warp?.items).filter(i=>i?.id&&i.state!=='retired'&&
    (i.state!=='done'||!Number.isFinite(timestamp(i.done_at||i.receipt?.at))||referenceNow()-timestamp(i.done_at||i.receipt?.at)<=7*86400000));
  const byId=new Map(items.map(i=>[i.id,i]));
  const goals=array(state.warp?.goals).map(g=>({...g,type:'goal',id:g.id||`goal:${g.title}`}));
  const topicOf=i=>array(i.topics)[0]||'unnamed';
  // Sector order: the heddles as the loom holds them, then any topic an item named for itself.
  const order=array(state.heddles).map(h=>h.slug).filter(Boolean);
  for(const i of [...items,...goals]) { const t=topicOf(i); if(!order.includes(t)) order.push(t); }
  // A topic with no room gets no sector: an empty wedge would be a placeholder, which the law forbids.
  const sectors=order.map(slug=>({slug,
    members:[...items,...goals].filter(i=>topicOf(i)===slug),
    refs:items.filter(i=>array(i.topics).slice(1).includes(slug))}))
    .filter(s=>s.members.length||s.refs.length);
  for(const s of sectors){
    s.cells=s.members.map(i=>({item:i,id:i.id,kind:'item',ring:i.type==='goal'?0:depthOf(i,byId),
      foot:FOOT[i.type]||FOOT.action,box:BOX[i.type]||BOX.action}));
    const rim=Math.max(1,...s.cells.filter(c=>c.item.type!=='goal').map(c=>c.ring));
    for(const c of s.cells) if(c.item.type==='goal') c.ring=rim+1;   // a goal sits at the sector's outer rim
    for(const i of s.refs) s.cells.push({item:i,id:`ref:${s.slug}:${i.id}`,target:i.id,kind:'reference',
      ring:depthOf(i,byId),foot:FOOT.reference,box:BOX.reference});
    s.weight=s.cells.reduce((n,c)=>n+c.foot+GUTTER,0)||FOOT.action;
  }
  // The angle a topic owns is its share of the population; the wall between sectors is a real gap.
  const total=sectors.reduce((n,s)=>n+s.weight,0)||1;
  const wall=sectors.length>1?Math.min(.1,1.1/sectors.length):0;
  const usable=Math.PI*2-wall*sectors.length;
  for(const s of sectors) s.span=Math.max(usable*SECTOR_FLOOR,usable*(s.weight/total));
  const claimed=sectors.reduce((n,s)=>n+s.span,0)||1;
  let cursor=-Math.PI/2;
  for(const s of sectors){s.span*=usable/claimed;s.from=cursor;s.mid=cursor+s.span/2;cursor+=s.span+wall;}
  // Ring radius: whatever the busiest sector needs for its rooms to stand side by side on that ring.
  const deepest=Math.max(1,...sectors.flatMap(s=>s.cells.map(c=>c.ring)));
  for(let k=1;k<=deepest;k++){
    let need=k===1?RING1_MIN:0;
    for(const s of sectors){
      const arc=s.cells.filter(c=>c.ring===k).reduce((n,c)=>n+c.foot+GUTTER,0);
      if(arc) need=Math.max(need,arc/s.span);
    }
    rings[k]=Math.max(need,rings[k-1]+RING_GAP);
  }
  rings[0]=rings[1]*HUB_FRACTION;              // the hub is a fraction of ring 1, so the frame never changes
  const unit=rings[1]/RING1_MIN;
  fixed.forEach((id,i)=>{const a=-Math.PI/2+i*Math.PI/4;
    add({id,kind:'hub',title:id,x:Math.cos(a)*rings[0],y:Math.sin(a)*rings[0],unit,
      w:(id==='archive'?96:86)*unit,h:(id==='shed'?52:42)*unit});});
  const crew=rooms.find(r=>r.id==='crew'),crewAngle=crew?Math.atan2(crew.y,crew.x):Math.PI/2;
  array(state.hud?.strands).forEach((strand,i)=>{   // a strand's plaque hangs in the crew room
    const lane=(i%2?1:-1)*Math.ceil((i+1)/2)*62*unit,depth=rings[0]*1.5;
    add({id:`strand:${strand.id}`,title:strand.title||strand.id,kind:'strand',strand,unit,
      x:Math.cos(crewAngle)*depth-Math.sin(crewAngle)*lane,y:Math.sin(crewAngle)*depth+Math.cos(crewAngle)*lane,
      w:84*unit,h:44*unit});
    edges.push({from:'crew',to:`strand:${strand.id}`,kind:'passage'});});
  const angleOf=new Map();
  for(const s of sectors){
    s.arcs=[];
    for(let k=1;k<=deepest;k++){
      const cells=s.cells.filter(c=>c.ring===k).sort((a,b)=>{
        const pa=array(a.item.needs).map(id=>angleOf.get(id)).find(number),
              pb=array(b.item.needs).map(id=>angleOf.get(id)).find(number);
        return number(pa)&&number(pb)&&pa!==pb?pa-pb:collator.compare(a.id,b.id);});
      if(!cells.length)continue;
      const r=rings[k],wanted=cells.reduce((n,c)=>n+c.foot+GUTTER,0)/r;
      // A ring that is not full spreads across its sector: a few rooms make a ring, not a clump.
      const stretch=wanted<s.span?s.span/wanted:1,squeeze=wanted>s.span?s.span/wanted:1;
      let t=s.from;
      for(const c of cells){
        const step=(c.foot+GUTTER)/r*squeeze*stretch,a=t+step/2;t+=step;
        angleOf.set(c.id,a);
        add({id:c.id,title:c.item.title||c.id,item:c.kind==='item'?c.item:null,target:c.target,
          kind:c.kind,wing:s.slug,ring:k,angle:a,unit,
          x:Math.cos(a)*r,y:Math.sin(a)*r,w:c.box[0],h:c.box[1]});
      }
      s.arcs.push({r,from:angleOf.get(cells[0].id),to:angleOf.get(cells.at(-1).id)});
      // The ring itself is a corridor: the hand walks sideways along it.
      cells.forEach((c,i)=>{if(i)edges.push({from:cells[i-1].id,to:c.id,kind:'ring'});});
    }
    const lip=rings[0]+(rings[1]-rings[0])*.44;
    const wing={id:`wing:${s.slug}`,slug:s.slug,title:s.slug,kind:'wing',angle:s.mid,ring:0,unit,
      x:Math.cos(s.mid)*lip,y:Math.sin(s.mid)*lip,w:120*unit,h:26*unit,
      from:s.from,span:s.span,arcs:s.arcs,extent:rings[Math.max(...s.cells.map(c=>c.ring))],
      population:s.cells.length};
    wings.push(wing);add(wing);
    const mouth=rooms.filter(r=>r.kind==='hub').sort((a,b)=>Math.hypot(a.x-wing.x,a.y-wing.y)-Math.hypot(b.x-wing.x,b.y-wing.y))[0];
    if(mouth)edges.push({from:mouth.id,to:wing.id,kind:'passage'});
    for(const c of s.cells){
      const needs=c.kind==='item'?array(c.item.needs).filter(id=>byId.has(id)):[];
      if(needs.length)needs.forEach(id=>edges.push({from:id,to:c.id,kind:'needs'}));
      else edges.push({from:wing.id,to:c.id,kind:'spur'});
    }
  }
  roomById=new Map(rooms.map(r=>[r.id,r]));
  fixed.forEach((id,i)=>edges.push({from:id,to:fixed[(i+1)%fixed.length],kind:'passage'}));
  if(!roomById.has(selected))selected='shed';
  const select=$('wing'),old=select.value;select.replaceChildren(element('option','Wings'));select.firstChild.value='';
  for(const wing of wings){const option=element('option',`${wing.slug} · ${wing.population}`);option.value=wing.id;select.append(option);}select.value=old;
  $('count').textContent=`${items.length} rooms · ${wings.length} wings · ${rings.length-1} rings`;
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
  if(!r.item||r.ring===1||seen.has(r.id)||opened.has(r.id))return false;
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
    // The sector is a wedge sized by its population: the wall between topics is real, the floor is not paint.
    ctx.beginPath();ctx.arc(0,0,rings[0]*1.16,wing.from,wing.from+wing.span);
    ctx.arc(0,0,wing.extent+140*wing.unit,wing.from+wing.span,wing.from,true);ctx.closePath();
    ctx.fillStyle='#0f0c0728';ctx.fill();ctx.strokeStyle='#3c2f1a';ctx.lineWidth=.7/camera.z;ctx.stroke();
    for(const arc of wing.arcs||[]){   // the ring corridor: the hand walks it sideways
      ctx.beginPath();ctx.arc(0,0,arc.r,arc.from,arc.to);ctx.strokeStyle='#4e3c1f';ctx.lineWidth=.9/camera.z;ctx.stroke();
    }
  }
  ctx.beginPath();ctx.arc(0,0,rings[0]*1.16,0,Math.PI*2);ctx.strokeStyle='#5b4724';ctx.lineWidth=.9/camera.z;ctx.stroke();
  for(const e of edges){
    if(e.kind==='ring')continue;                       // drawn as the sector's arc, not as a chord
    const a=roomById.get(e.from),b=roomById.get(e.to);if(!a||!b)continue;
    const shut=e.kind==='needs'&&fogged(b),lit=e.kind==='needs'&&(visited(a.item||{})||visited(b.item||{}))&&!shut;
    const dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy)||1,ux=dx/length,uy=dy/length;
    const trimA=Math.min(a.w/(2*Math.max(.01,Math.abs(ux))),a.h/(2*Math.max(.01,Math.abs(uy)))),trimB=Math.min(b.w/(2*Math.max(.01,Math.abs(ux))),b.h/(2*Math.max(.01,Math.abs(uy))));
    let start={x:a.x+ux*trimA,y:a.y+uy*trimA};const end={x:b.x-ux*trimB,y:b.y-uy*trimB};
    if(e.kind==='spur'){   // a door off the ring corridor, not a rope back to the hub
      const inner={x:b.x-b.x/Math.hypot(b.x,b.y)*(trimB+13),y:b.y-b.y/Math.hypot(b.x,b.y)*(trimB+13)};
      ctx.strokeStyle=visited(b.item||{})?'#8a6a30':'#3c2f1a';ctx.lineWidth=.9/Math.max(.6,camera.z);
      ctx.beginPath();ctx.moveTo(inner.x,inner.y);ctx.lineTo(b.x-b.x/Math.hypot(b.x,b.y)*trimB,b.y-b.y/Math.hypot(b.x,b.y)*trimB);ctx.stroke();
      continue;
    }
    const door={x:start.x+(end.x-start.x)*.72,y:start.y+(end.y-start.y)*.72},k=Math.max(1,.9/camera.z);
    ctx.strokeStyle=lit?'#cf963e90':e.kind==='needs'?'#7c5c2b':'#3a2d19';ctx.lineWidth=(e.kind==='needs'?1.1:.65)/Math.max(.6,camera.z);ctx.shadowColor='#f2b13444';ctx.shadowBlur=lit?5:0;
    ctx.beginPath();ctx.moveTo(start.x,start.y);if(shut){ctx.lineTo(door.x-ux*7*k,door.y-uy*7*k);ctx.moveTo(door.x+ux*7*k,door.y+uy*7*k);}ctx.lineTo(end.x,end.y);ctx.stroke();ctx.shadowBlur=0;
    // A shut door is a signal, not scenery: it keeps its size on the glass however far out the ring is.
    if(shut){ctx.save();ctx.translate(door.x,door.y);ctx.rotate(Math.atan2(dy,dx));ctx.scale(k,k);
      ctx.fillStyle=colors.ink;ctx.fillRect(-5,-10,10,20);ctx.strokeStyle=blockers(b).length?colors.ice:'#d4a357';
      ctx.lineWidth=1.2;ctx.strokeRect(-3,-9,6,18);ctx.beginPath();ctx.moveTo(-3,0);ctx.lineTo(3,0);ctx.stroke();ctx.restore();}
  }
  // The last measured beads, as footprints: the resident's own trace, so amber.
  footprints.forEach((b,i)=>{const p=roomPoint(placeOf(b));if(!p)return;ctx.globalAlpha=(i+1)/footprints.length*.38;ctx.fillStyle=colors.amber;ctx.beginPath();ctx.arc(p.x-24+i*4,p.y+23,1.2,0,Math.PI*2);ctx.fill();});ctx.globalAlpha=1;
  const actorRoom=state.run&&state.shuttle?.state!=='released'?roomById.get(actor):null;
  for(const r of rooms){
    const sx=(r.x-camera.x)*camera.z+width/2,sy=(r.y-camera.y)*camera.z+height/2;
    if(sx+r.w*camera.z<0||sx-r.w*camera.z>width||sy+r.h*camera.z<0||sy-r.h*camera.z>height)continue;
    const hand=r.id===selected,shut=fogged(r),warm=visited(r.item||{}),resident=r.id===actorRoom?.id;
    const hubLit=r.kind==='hub'&&(resident||array(state.beads).some(b=>placeOf(b)===r.id));
    const lit=(warm||hubLit)&&!shut,done=r.item?.state==='done';
    const his=r.item?.type==='decision'&&r.item.state==='ready';   // the hand's door: ice, by the colour law
    const furniture=r.kind==='hub'||r.kind==='strand'||r.kind==='wing';
    const left=r.x-r.w/2,top=r.y-r.h/2,tokens=(furniture?camera.z*r.unit:camera.z)<.55,beads=camera.z<.3;
    if(r.kind==='wing'){
      const size=Math.max(13*r.unit,11/camera.z);
      ctx.textAlign=Math.cos(r.angle)<-.3?'right':Math.cos(r.angle)>.3?'left':'center';
      drawText(`${r.title} · ${r.population}`,r.x,r.y,lit?colors.text:'#9c8a6b',size,600*r.unit);
      ctx.textAlign='left';continue;
    }
    // A room nobody has entered is dark: absent is black, never dim amber.
    const line=shut?'#221b12':lit?colors.amber:done?colors.ember:colors.absent;
    if(beads&&(r.kind==='item'||r.kind==='reference')){
      /* A crowded ring reads as beads on its corridor: the population is the picture, and its
       * colour still says whose it is. Walking in (zoom) is what makes a room a room. */
      const dot=(r.item?.type==='goal'?9:r.kind==='reference'?3.5:6)/camera.z;
      const fill=shut?'#241c12':his?colors.ice:lit?colors.amber:done?colors.ember:colors.absent;
      ctx.beginPath();ctx.arc(r.x,r.y,dot,0,Math.PI*2);
      if(r.item?.type==='goal'){ctx.strokeStyle=fill;ctx.lineWidth=2.4/camera.z;ctx.stroke();}
      else{ctx.fillStyle=fill;ctx.fill();}
      if(his&&!shut){ctx.strokeStyle=colors.ice;ctx.lineWidth=1.1/camera.z;ctx.beginPath();ctx.arc(r.x,r.y,dot*1.9,0,Math.PI*2);ctx.stroke();}
      if(hand){ctx.strokeStyle=colors.ice;ctx.lineWidth=2/camera.z;ctx.beginPath();ctx.arc(r.x,r.y,dot*2.9,0,Math.PI*2);ctx.stroke();}
      if(resident)glow(r.x,r.y,30/camera.z,.22);
      continue;
    }
    if(lit||resident)glow(r.x,r.y,Math.max(r.w,r.h)*.95,resident?.2:.08);
    ctx.fillStyle=colors.ink;chamber(r);ctx.fill();
    ctx.strokeStyle=r.kind==='hub'&&!lit?'#5b4724':line;ctx.lineWidth=(lit?1.15:.85)/Math.max(.55,camera.z);ctx.shadowColor='#f2b13477';ctx.shadowBlur=lit?9:0;chamber(r);ctx.stroke();ctx.shadowBlur=0;
    if(his&&!shut){ctx.strokeStyle=colors.ice;ctx.lineWidth=1.25/Math.max(.55,camera.z);ctx.shadowColor='#8fd3ff55';ctx.shadowBlur=7;chamber(r);ctx.stroke();ctx.shadowBlur=0;}
    if(hand){ // the hand: corner brackets, ice, never a fill
      const c=Math.max(9,7/camera.z),o=5/Math.max(.55,camera.z);ctx.strokeStyle=colors.ice;ctx.lineWidth=1.4/Math.max(.55,camera.z);
      for(const [mx,my] of [[-1,-1],[1,-1],[1,1],[-1,1]]){const px=r.x+mx*(r.w/2+o),py=r.y+my*(r.h/2+o);
        ctx.beginPath();ctx.moveTo(px-mx*c,py);ctx.lineTo(px,py);ctx.lineTo(px,py-my*c);ctx.stroke();}
    }
    if(r.kind==='reference'){if(!tokens)drawText(r.title,left+6,r.y+4,colors.absentText,Math.max(9,8/camera.z),r.w-12);continue;}
    if(r.kind==='strand'){
      const window=bindingWindow(r.strand.bucket);
      // The feed names no bucket for a strand yet (report, open); its allowance is the reading it does carry.
      const allowance=number(r.strand.allowance)&&r.strand.allowance>0&&number(r.strand.spent)
        ? {name:'allowance',pct_left:Math.max(0,100-100*r.strand.spent/r.strand.allowance)} : null;
      const vial=window&&number(window.pct_left)?window:allowance,pct=vial?vial.pct_left:null;
      const heat=pct===null?colors.absent:tempColor(pct);   // a starved strand reads cold
      if(tokens){drawText('▱',r.x-6*r.unit,r.y+5*r.unit,heat,Math.max(14*r.unit,12/camera.z));continue;}
      drawText('▱ '+r.title,left+7*r.unit,top+15*r.unit,heat,Math.max(8*r.unit,8/camera.z),r.w-14*r.unit);
      ctx.fillStyle='#1b150d';ctx.fillRect(left+7*r.unit,top+25*r.unit,r.w-14*r.unit,2.4*r.unit);
      if(pct!==null){ctx.fillStyle=heat;ctx.fillRect(left+7*r.unit,top+25*r.unit,(r.w-14*r.unit)*Math.max(0,Math.min(100,pct))/100,2.4*r.unit);
        drawText(`${vial.name} ${Math.round(pct)}%`,left+7*r.unit,top+38*r.unit,heat,Math.max(7*r.unit,7/camera.z),r.w-14*r.unit);}
      else drawText('fuel unmeasured',left+7*r.unit,top+38*r.unit,colors.absentText,Math.max(7*r.unit,7/camera.z),r.w-14*r.unit);
      continue;
    }
    if(r.kind==='hub'){
      if(r.id==='shed'&&state.shuttle?.state==='listening'){   // the watchtower
        const u=r.unit;ctx.strokeStyle=colors.amber;ctx.lineWidth=1.2/Math.max(.55,camera.z);ctx.beginPath();
        ctx.moveTo(left+9*u,top);ctx.lineTo(left+9*u,top-14*u);ctx.lineTo(left+15*u,top-14*u);ctx.lineTo(left+15*u,top-7*u);
        ctx.lineTo(left+21*u,top-7*u);ctx.lineTo(left+21*u,top-14*u);ctx.lineTo(left+27*u,top-14*u);ctx.lineTo(left+27*u,top);ctx.stroke();}
      drawText(r.title,left+8*r.unit,r.y+(resident?14:4)*r.unit,lit?colors.text:'#9c8a6b',Math.max(11*r.unit,9/camera.z),r.w-16*r.unit);
      if(!resident&&!tokens){const out=Math.hypot(r.x,r.y)||1,ox=r.x/out,oy=r.y/out;
        ctx.textAlign=ox>.3?'left':ox<-.3?'right':'center';
        drawText(hubDetail(r.id),r.x+ox*(r.w/2+8*r.unit),r.y+oy*(r.h/2+12*r.unit)+4*r.unit,lit?'#9c8a6b':colors.absentText,Math.max(7*r.unit,7/camera.z),r.w+40*r.unit);ctx.textAlign='left';}
      continue;
    }
    const item=r.item;
    if(shut){drawText('▣ '+r.id,left+7,r.y+4,'#4d3f28',Math.max(9,8/camera.z),r.w-14);continue;}
    const label=his?colors.ice:hand?colors.ice:lit?colors.text:done?'#8d754a':colors.absentText;
    drawText(`${symbols[item.type]||'·'} ${r.id}`,left+7,top+(tokens?r.h/2+4:16),label,Math.max(9,8/camera.z),r.w-13);
    if(!tokens){
      if(r.w>=120)wrap(item.title,left+7,top+32,r.w-14,item.type==='goal'?2:2,lit||his?colors.text:colors.absentText,9);
      else drawText(item.title,left+7,top+30,lit||his?'#c9bda2':colors.absentText,8,r.w-13);
      if(item.type==='goal')drawText(item.metric||'metric unmeasured',left+7,top+r.h-9,item.metric?'#9c8a6b':colors.absentText,8,r.w-14);
      if(done&&item.receipt)drawText(receiptText(item.receipt),left+7,top+r.h-8,'#8d754a',8,r.w-14);
      if(his){const out=opens(r);
        drawText(`opens ${out.join(', ')||'—'}`,left+4,top+r.h+13,colors.ice,Math.max(9,8/camera.z),Math.max(r.w,120));
        if(camera.z>=.8)drawText(`stake ${item.stake??'?'}`,left+4,top+r.h+25,item.stake?'#9c8a6b':colors.absentText,8,r.w+60);}
    }
    if(item.taken&&item.taken!==state.run?.id)drawText('▱',r.x+r.w/2-11,r.y-5,colors.amber,Math.max(13,11/camera.z));
  }
  if(actorRoom){const p=actorPoint||actorRoom;glow(p.x,p.y,48*(actorRoom.unit||1),.13);ctx.shadowColor='#f2b13499';ctx.shadowBlur=10;ctx.textAlign='center';
    drawText(state.run?.mood_glyph||'b·_·d',p.x,p.y-4*(actorRoom.unit||1),colors.amber,Math.max(14*(actorRoom.unit||1),12/camera.z));ctx.textAlign='left';ctx.shadowBlur=0;}
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
  $('map-caption').textContent=width<620?`RING ${roomById.get(selected)?.ring??0}`:`THE WARP / RING ${roomById.get(selected)?.ring??0} · DEPTH IS DEPENDENCY, ANGLE IS TOPIC`;
}
function receiptText(receipt){if(typeof receipt==='string')return receipt;return [receipt.kind,receipt.number!=null?`#${receipt.number}`:null,receipt.action,receipt.commit?.slice(0,8)].filter(Boolean).join(' ')||'receipt recorded';}
function renderPanels(){
  const shuttle=state.shuttle||{},last=array(state.beads).at(-1), place=shuttle.state==='listening'?'shed':shuttle.place||placeOf(last)||'shed';
  $('glyph').textContent=state.run?.mood_glyph||'b·_·d';$('glyph').title=`Locate resident: ${place}`;$('state').textContent=shuttle.state||'unmeasured';
  const alive=['awake','listening','working','thinking'].includes(shuttle.state);
  $('state').classList.toggle('unmeasured',!alive);$('glyph').classList.toggle('unmeasured',!alive);
  $('act').textContent=shuttle.state==='listening'?'listening · shed → wire':last?`${last.act||'act'} · ${place}`:state.run?`at ${place} · no act reading`:'No resident reading';
  const since=shuttle.since||state.run?.started;
  $('since').textContent=since&&Number.isFinite(timestamp(since))?`since ${new Date(timestamp(since)).toISOString().slice(11,16)} UTC`:'';
  $('waiting').textContent=shuttle.waiting_on?`waiting on ${shuttle.waiting_on}`:shuttle.state==='held'?`held · ${shuttle.why||'reason unmeasured'}`:'';
  $('fuel').replaceChildren();
  const buckets=[...array(state.fuel?.buckets)].sort((a,b)=>Number(!!b.seat)-Number(!!a.seat));
  if(!buckets.length)$('fuel').append(element('p','No fuel reading','unmeasured'));
  for(const bucket of buckets){
    const block=element('div',null,`bucket${bucket.seat?'':' secondary'}`),name=element('div',bucket.name,'bucket-name');name.append(element('span',bucket.seat?'SEAT':'BUCKET'));block.append(name);
    for(const window of array(bucket.windows)){
      const row=element('div',null,`window${window.binding?' binding':''}`),heading=element('div',null,'window-heading'),value=element('strong',number(window.pct_left)?String(Math.round(window.pct_left)):'?');
      // The vial cools as it empties: a window's colour is its own reading, never a status word.
      const heat=tempColor(window.pct_left);value.style.color=heat;if(!number(window.pct_left))value.classList.add('unmeasured');
      value.append(element('small',number(window.pct_left)?'%':''));heading.append(element('span',window.name||'window'),value);row.append(heading);
      if(number(window.pct_left)){const bar=element('div',null,'bar'),fill=element('i');fill.style.width=`${Math.min(100,Math.max(0,window.pct_left))}%`;fill.style.background=heat;bar.append(fill);row.append(bar);}
      else row.append(element('div',null,'bar'));
      const reset=timestamp(window.resets_at), seconds=Number.isFinite(reset)?(reset-referenceNow())/1000:null;
      row.append(element('div',seconds===null?'↻ reset unmeasured':seconds<=0?'↻ reset due · awaiting reading':`↻ resets in ${duration(seconds)}`,seconds===null?'reset unmeasured':'reset'));
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
  if(!pack){target.append(element('p','No pack reading','unmeasured'));return;}
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
function ingest(next){state=next;const first=!initialized;if(first)resize();buildMap();updateActor(first);renderPanels();selection();
  if(first){initialized=true;const door=rooms.find(r=>r.item?.type==='decision'&&r.item.state==='ready');choose(door?.id||'shed',false);frameFirstView();selection();
    // The glass settles after the panels take their text: frame once more on the layout that exists.
    requestAnimationFrame(()=>{resize();frameFirstView();});}
  $('source').textContent=fixture?`${fixture.toUpperCase()} FIXTURE · frozen`:`${state.repo||'local'} · live`;
  canvas.dataset.ready='true';canvas.dataset.actor=actor;canvas.dataset.rooms=String(rooms.length);requestDraw();
}
let drag=null;
canvas.addEventListener('pointerdown',event=>{canvas.focus();canvas.setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY,cx:camera.x,cy:camera.y,moved:false};});
canvas.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>4)drag.moved=true;camera.x=drag.cx-dx/camera.z;camera.y=drag.cy-dy/camera.z;requestDraw();});
canvas.addEventListener('pointerup',event=>{if(!drag)return;const moved=drag.moved;drag=null;if(moved)return;const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left-width/2)/camera.z+camera.x,y=(event.clientY-rect.top-height/2)/camera.z+camera.y;const r=[...rooms].reverse().find(r=>Math.abs(x-r.x)<=r.w/2&&Math.abs(y-r.y)<=r.h/2);if(r){const again=selected===r.id;choose(r.id,false);if(again)activate(r);}});
canvas.addEventListener('pointercancel',()=>{drag=null;});
canvas.addEventListener('wheel',event=>{event.preventDefault();if(event.ctrlKey||event.metaKey){camera.z=Math.max(.06,Math.min(2.2,camera.z*(event.deltaY>0?.9:1.1)));}else{camera.x+=event.deltaX/camera.z;camera.y+=event.deltaY/camera.z;}requestDraw();},{passive:false});
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
$('glyph').onclick=()=>{if(roomById.has(actor)){choose(actor);camera.z=Math.max(camera.z,width<600?.85:1);requestDraw();}};
$('hub').onclick=()=>{choose('shed',false);frameFirstView();};
$('doors').onclick=()=>{const doors=rooms.filter(r=>r.item?.type==='decision'&&r.item.state==='ready');if(doors.length){const next=doors[(doors.findIndex(r=>r.id===selected)+1)%doors.length];choose(next.id);camera.z=Math.max(camera.z,width<600?.85:1);requestDraw();}else{$('selection').replaceChildren(element('span','No ready decisions in this reading.'));}};
$('wing').onchange=event=>{if(event.target.value){const w=roomById.get(event.target.value);choose(event.target.value);camera.z=Math.min(1.4,(width-40)/((w?.extent||rings[1])*1.1));requestDraw();}canvas.focus();};
$('zoom-in').onclick=()=>{camera.z=Math.min(2.2,camera.z*1.25);requestDraw();};$('zoom-out').onclick=()=>{camera.z=Math.max(.06,camera.z/1.25);requestDraw();};
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
