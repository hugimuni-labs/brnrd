// Visual rehearsal for field6's [ACTOR] block — the camera's detents, the
// tiled face, and the room a hand can choose. Inspect every saved image.
//
//   FIXTURE=live.json python3 dev/serve.py &        (or any server that maps
//                                                    /loom/state.json to a feed)
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs> \
//   LOOM_URL=http://127.0.0.1:7797 OUT=/tmp/actorshots node field6-shots.mjs
//
// NEVER quote a frame time from plain headless chromium on this machine: it
// falls back to SwiftShader, every full-screen composite costs ~12 ms, and the
// post-pass reads as a slideshow at ~15 fps. That is the renderer, not the
// scene. headless:false + --use-angle=metal is what measured below.
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE
  || '/Users/gurio/Source/Projects/brnrd/src/frontend/node_modules/playwright/index.mjs'));
const base = process.env.LOOM_URL || 'http://127.0.0.1:7797';
const out  = process.env.OUT || '/tmp/actorshots';
const page6 = q => `${base}/loom/dev/field6.html${q?'?'+q:''}`;
await mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const errors=[], receipts={};
const ctx = dsf => browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:dsf});
const settle = p => p.waitForTimeout(4200);
const c = await ctx(1), p = await c.newPage();
p.on('pageerror',e=>errors.push(e.message));
await p.goto(page6(),{waitUntil:'load'}); await settle(p);

// a cut of the frame, for the close-ups: the same canvas, one region
// a crop of the frame. `s` magnifies with smoothing OFF — nearest-neighbour, so
// a close-up of the face shows the cells that are there and invents no detail.
const cut = (px,py,w,h,s=1) => p.evaluate(([x0,y0,w,h,s])=>{
  const c=document.createElement('canvas');c.width=w*s;c.height=h*s;
  const g=c.getContext('2d');g.imageSmoothingEnabled=false;
  g.drawImage(document.getElementById('c'),
    x0*devicePixelRatio,y0*devicePixelRatio,w*devicePixelRatio,h*devicePixelRatio,0,0,w*s,h*s);
  return c.toDataURL('image/png');},[px,py,w,h,s]);
const png = async (name,data) =>
  writeFile(resolve(out,name+'.png'),Buffer.from(data.split(',')[1],'base64'));

// ── 1 · the three detents, by their own keys ────────────────────────────────
receipts.detents=[];
for (const [key,want] of [['1','interior'],['2','mid'],['3','schematic']]) {
  await p.keyboard.press(key); await p.waitForTimeout(1600);
  const s=[]; for(let i=0;i<4;i++){await p.waitForTimeout(1000);s.push(await p.evaluate(()=>window.__fx));}
  const r=await p.evaluate(()=>({z:+CAM.z.toFixed(4),tz:+CAM.tz.toFixed(4),
    ...ACTOR.camera.state(),detent:ACTOR.camera.state().detent.k}));
  receipts.detents.push({key,range:r.range,z:r.z,resting:r.resting,
    fps_min:Math.min(...s.map(v=>v.fps)),
    frameMs:+(s.reduce((a,v)=>a+v.frameMs,0)/s.length).toFixed(3),
    worldMs:+(s.reduce((a,v)=>a+v.worldMs,0)/s.length).toFixed(3)});
  assert.equal(r.range,want,`key ${key} must rest at ${want}`);
  assert.ok(r.resting,`key ${key} must come to rest`);
  await p.screenshot({path:resolve(out,`detent-${key}-${want}.png`)});
}

// ── 2 · the wheel eases into the nearest rest, and scrolling through does not ─
{
  await p.keyboard.press('2'); await p.waitForTimeout(1500);
  await p.mouse.move(700,450);
  await p.mouse.wheel(0,-240);                       // a short push off the detent
  const mid=await p.evaluate(()=>({z:+CAM.z.toFixed(4),resting:ACTOR.camera.state().resting}));
  await p.waitForTimeout(2200);                      // hand off the wheel
  const after=await p.evaluate(()=>({z:+CAM.z.toFixed(4),tz:+CAM.tz.toFixed(4),
    range:ACTOR.camera.state().range,resting:ACTOR.camera.state().resting}));
  // and a long scroll must travel past a detent, not be caught by it
  await p.evaluate(()=>{CAM.tz=1;CAM.z=1;});
  await p.waitForTimeout(300);
  const through=await p.evaluate(async()=>{
    const seen=[];
    for(let i=0;i<24;i++){                            // one continuous gesture
      window.dispatchEvent(new WheelEvent('wheel',{deltaY:60,cancelable:true}));
      await new Promise(r=>requestAnimationFrame(r));
      seen.push(+CAM.tz.toFixed(3));
    }
    return {from:seen[0],to:seen.at(-1),passed:seen.some(v=>Math.abs(v-0.46)<0.06)};
  });
  await p.waitForTimeout(2200);
  const landed=await p.evaluate(()=>({z:+CAM.z.toFixed(4),range:ACTOR.camera.state().range,
    resting:ACTOR.camera.state().resting}));
  // and a controlled push, one event, that stays nearer mid than interior
  await p.evaluate(()=>{CAM.tz=0.46;CAM.z=0.46;});
  await p.waitForTimeout(400);
  const push=await p.evaluate(async()=>{
    window.dispatchEvent(new WheelEvent('wheel',{deltaY:-90,cancelable:true}));
    await new Promise(r=>requestAnimationFrame(r));
    return {tz:+CAM.tz.toFixed(4)};
  });
  await p.waitForTimeout(2200);
  const nearest=await p.evaluate(()=>({z:+CAM.z.toFixed(4),range:ACTOR.camera.state().range,
    resting:ACTOR.camera.state().resting}));
  receipts.wheel={nudged:mid,rested:after,through,landed,push,nearest};
  assert.equal(nearest.range,'mid','a small push must rest back on the nearest detent');
  assert.ok(after.resting,'the wheel must come to rest on a detent');
  assert.ok(through.passed,'a continuous scroll must be able to pass through mid');
}

// ── 3 · the face, at two ranges ─────────────────────────────────────────────
receipts.face={};
for (const [key,label] of [['1','interior'],['3','schematic']]) {
  await p.keyboard.press(key); await p.waitForTimeout(1800);
  const f=await p.evaluate(()=>{const me=pods.find(q=>q.kind==='resident');
    return {x:Math.round(me.x),y:Math.round(me.y),glyph:me.glyphNow,
            src:me.face&&me.face.src,cells:me.face?me.face.cells.map(c=>c.ch).join(''):null};});
  receipts.face[label]=f;
  await png(`face-${label}`, await cut(f.x-56,f.y-34,112,68,4));
}
// the retype, caught mid-cell: a fresh mood from the feed repaints tile by tile
{
  await p.keyboard.press('1'); await p.waitForTimeout(1500);
  const me=await p.evaluate(()=>{const q=pods.find(v=>v.kind==='resident');
    return {x:Math.round(q.x),y:Math.round(q.y)};});
  const seq=await p.evaluate(async()=>{
    const q=pods.find(v=>v.kind==='resident');
    S.run.mood_glyph='b^o^d';                        // as a feed refresh would
    const rows=[];
    for(let i=0;i<7;i++){
      await new Promise(r=>requestAnimationFrame(r));
      const t=performance.now();
      rows.push({t:Math.round(t),
        cells:q.face.cells.map(c=>`${c.prev}→${c.ch}`).join(' '),
        age:q.face.cells.map(c=>Math.round(t-c.t0))});
      await new Promise(r=>setTimeout(r,38));
    }
    return rows;
  });
  receipts.face.retype=seq.map(r=>({cells:r.cells,age_ms:r.age}));
  for(let i=0;i<6;i++){
    await png(`retype-${i}`, await cut(me.x-44,me.y-26,88,52,4));
    await p.waitForTimeout(40);
  }
  await png('face-retype', await cut(me.x-56,me.y-34,112,68,4));
  await p.evaluate(()=>{S.run.mood_glyph='b·w·d';});
}

// ── 4 · the selection flow, with the un-wired verb visible ──────────────────
{
  await p.keyboard.press('3'); await p.waitForTimeout(1800);
  const target=await p.evaluate(()=>{
    const r=rooms.find(q=>q.opens.length&&q.it.state==='ready')||rooms[0];
    const {x,y}=roomXY(r,CAM.z);return {id:r.it.id,x:Math.round(x),y:Math.round(y)};});
  await p.mouse.click(target.x,target.y); await p.waitForTimeout(700);
  const sel=await p.evaluate(()=>({kind:SELECTED.kind,id:SELECTED.id,slug:SELECTED.slug}));
  assert.equal(sel.id,target.id,'a click must select the room under it');
  await p.screenshot({path:resolve(out,'select-room.png')});
  await png('select-panel', await cut(1440-382,900-270,378,258,2));

  // the offered verb, pressed the way a stranger would: with the mouse
  const btn=await p.evaluate(()=>({x:Math.round(BTN.x+BTN.w/2),y:Math.round(BTN.y+BTN.h/2),on:BTN.on}));
  assert.ok(btn.on,'the offer must publish a rectangle a hand can reach');
  await p.mouse.click(btn.x,btn.y); await p.waitForTimeout(900);
  const staged=await p.evaluate(()=>[...SELECTED.chosen]);
  assert.deepEqual(staged,[target.id],'⏎ must stage the intent');
  await p.screenshot({path:resolve(out,'select-chosen.png')});
  await png('chosen-panel', await cut(1440-382,900-270,378,258,2));
  const hub=await p.evaluate(()=>{const c=hubBy.get('crew');const q=cellXY(c,CAM.z);
    return {x:Math.round(q.x),y:Math.round(q.y)};});
  await png('chosen-ready-pod', await cut(hub.x-110,hub.y-150,220,150,2));

  // the walk: ← → along the lane, ↑ ↓ across lanes
  const walk=[];
  for(const k of ['ArrowRight','ArrowRight','ArrowDown','ArrowLeft','ArrowUp']){
    await p.keyboard.press(k); await p.waitForTimeout(160);
    walk.push(k+' → '+await p.evaluate(()=>{const r=byItem.get(SELECTED.id);
      return r?`${r.it.id} lane ${LANES[r.lane].k} deck ${r.deck.slug}`:'—';}));
  }
  receipts.walk=walk;
  await p.screenshot({path:resolve(out,'select-walked.png')});

  // a deck is selectable, and says its own state
  const deck=await p.evaluate(()=>{const d=decks[Math.min(1,decks.length-1)];
    return {slug:d.slug,x:Math.round(d.x),y:Math.round(waterAt(CAM.z)+bandAt(CAM.z)+11)};});
  await p.mouse.click(deck.x,deck.y); await p.waitForTimeout(700);
  const dsel=await p.evaluate(()=>({kind:SELECTED.kind,slug:SELECTED.slug}));
  assert.equal(dsel.kind,'deck','the deck rail must select the deck');
  receipts.deck=dsel;
  await p.screenshot({path:resolve(out,'select-deck.png')});
  await png('deck-panel', await cut(1440-382,900-270,378,258,2));

  // and the honest refusal at interior, where the warp is not on screen
  await p.keyboard.press('1'); await p.waitForTimeout(1600);
  await p.mouse.click(400,300); await p.waitForTimeout(500);
  receipts.interior_hint=await p.evaluate(()=>SELECTED.hint);
  await p.screenshot({path:resolve(out,'interior-click.png')});
}

await c.close(); await browser.close();
assert.deepEqual(errors,[]);
console.log(JSON.stringify({receipts,out},null,1));
