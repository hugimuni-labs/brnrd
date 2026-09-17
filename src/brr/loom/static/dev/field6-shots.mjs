// Visual rehearsal for field6 — the layer pass, the record, the actor.
// MERGED 2026-09-17 by the layer pass: `d156ea27` committed this file with its
// conflict markers still in it, so it has not parsed — and therefore has not
// run — since that merge. Both halves are kept whole; the [ACTOR] half runs
// first on the top-level page, the [RECORD] half opens its own contexts, and
// the layer sections are appended at the end.
//
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
//
// Visual rehearsal for field6's [RECORD] block — the cloth, the fold, the body,
// the wire, the tint. Every direction of the fold is driven by a REAL click or
// keypress, not by writing the block's state, so the shot is a receipt for the
// interaction and not just for the renderer.
//
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs> \
//   LOOM_URL=http://127.0.0.1:7796 OUT=/tmp/recordshots node field6-shots.mjs
//
// Headless chromium on this machine falls back to SwiftShader, where every
// full-screen composite costs ~12 ms and the post-pass reads as a slideshow.
// That is the renderer, not the scene: headless:false + --use-angle=metal is
// the only configuration whose frame numbers may be quoted.
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE
  || '/Users/gurio/Source/Projects/brnrd/src/frontend/node_modules/playwright/index.mjs'));
const base = process.env.LOOM_URL || 'http://127.0.0.1:7797';   // the fixture feed
// the sweep needs a feed whose tick MOVES, which only the live daemon's does:
// serve the dev dir with no state.json of its own and the page falls through to
// 127.0.0.1:7777 the way FEEDS already says it will.
const live = process.env.LIVE_URL || 'http://127.0.0.1:7789';
const out  = process.env.OUT || '/tmp/actorshots';
const page6 = q => `${base}/loom/dev/field6.html${q?'?'+q:''}`;
await mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const errors=[], receipts={};
const ctx = dsf => browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:dsf});
const settle = p => p.waitForTimeout(4200);
const shot = (p,n) => p.screenshot({path:resolve(out,n+'.png')});
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
const cutOn = (pg,px,py,w,h,s=1) => pg.evaluate(([x0,y0,w,h,s])=>{
  const c=document.createElement('canvas');c.width=w*s;c.height=h*s;
  const g=c.getContext('2d');g.imageSmoothingEnabled=false;
  g.drawImage(document.getElementById('c'),
    x0*devicePixelRatio,y0*devicePixelRatio,w*devicePixelRatio,h*devicePixelRatio,0,0,w*s,h*s);
  return c.toDataURL('image/png');},[px,py,w,h,s]);
const png2 = async (pg,name,data) =>
  writeFile(resolve(out,name+'.png'),Buffer.from(data.split(',')[1],'base64'));
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

// ── 5 · the bench: the room's own body, opened over the ground ─────────────
{
  await p.keyboard.press('3'); await p.waitForTimeout(1500);
  const t=await p.evaluate(()=>{const r=rooms.find(q=>(q.it.title||'').length>40)||rooms[0];
    const {x,y}=roomXY(r,CAM.z);return {id:r.it.id,title:r.it.title,x:Math.round(x),y:Math.round(y)};});
  await p.mouse.click(t.x,t.y); await p.waitForTimeout(400);
  await p.screenshot({path:resolve(out,'bench-card-before.png')});
  await p.keyboard.press('Enter');                       // ⏎ opens the body
  await p.waitForFunction(id=>{const g=PAGES.get(id);return g&&g.state!=='loading';},t.id,{timeout:8000});
  await p.waitForTimeout(500);
  const pg=await p.evaluate(id=>{const g=PAGES.get(id);
    return {state:g.state,why:g.why||null,title:(g.d||{}).title||null,
            body:((g.d||{}).body||'').length,siblings:((g.d||{}).siblings||[]).length,
            lines:BENCH.total,fit:BENCH.fit};},t.id);
  receipts.bench={id:t.id,card_title:t.title,...pg};
  assert.equal(pg.state,'ok','the bench must read the item page');
  await p.screenshot({path:resolve(out,'bench-open.png')});
  await png('bench-body', await cut(Math.round(1440*0.05),Math.round(900*0.16),
    Math.round(1440*0.60),Math.round(900*0.71)));
  // a body long enough to need the scroll, so the scroll is actually measured
  const long=await p.evaluate(()=>{const r=byItem.get('w-80');
    if(!r)return null;ACTOR.select.room(r);ACTOR.bench.open('w-80');return 'w-80';});
  if(long){
    await p.waitForFunction(()=>{const g=PAGES.get('w-80');return g&&g.state!=='loading';},null,{timeout:8000});
    await p.waitForTimeout(600);
    for(let i=0;i<4;i++){await p.keyboard.press('ArrowDown');await p.waitForTimeout(120);}
    receipts.bench.long=await p.evaluate(()=>({id:'w-80',lines:BENCH.total,fit:BENCH.fit,scroll:BENCH.scroll}));
    await p.screenshot({path:resolve(out,'bench-scrolled.png')});
    await png('bench-long', await cut(Math.round(1440*0.05),Math.round(900*0.16),
      Math.round(1440*0.60),Math.round(900*0.71)));
    // nothing in the warp today is long enough to overflow a 900 px pane (the
    // longest body is 29 lines of 37 that fit), so the scroll is measured in a
    // window short enough to make it real rather than left unexercised
    await p.setViewportSize({width:1440,height:430}); await p.waitForTimeout(900);
    for(let i=0;i<4;i++){await p.keyboard.press('ArrowDown');await p.waitForTimeout(110);}
    receipts.bench.short_window=await p.evaluate(()=>({lines:BENCH.total,fit:BENCH.fit,scroll:BENCH.scroll}));
    assert.ok(receipts.bench.short_window.scroll>0,'a body past the pane must scroll');
    await p.screenshot({path:resolve(out,'bench-scrolled.png')});
    await p.setViewportSize({width:1440,height:900}); await p.waitForTimeout(900);
  }
  // a fetch that fails must SAY so, not show an empty page
  await p.evaluate(()=>{ACTOR.bench.open('w-999999');});
  await p.waitForFunction(()=>{const g=PAGES.get('w-999999');return g&&g.state!=='loading';},null,{timeout:8000});
  await p.waitForTimeout(400);
  receipts.bench.fail=await p.evaluate(()=>PAGES.get('w-999999'));
  assert.equal(receipts.bench.fail.state,'fail','a missing item must fail loudly');
  await png('bench-fail', await cut(Math.round(1440*0.05),Math.round(900*0.16),
    Math.round(1440*0.60),260));
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  assert.equal(await p.evaluate(()=>SELECTED.open),null,'esc must close the bench');
  // and the verb is still where it was, on `d` now that ⏎ opens
  await p.keyboard.press('d'); await p.waitForTimeout(300);
  receipts.bench.staged_with_d=await p.evaluate(()=>[...SELECTED.chosen]);
}
await c.close();

// ── 6 · the ship's sweep, against the live daemon's own tick ────────────────
{
  const c2=await ctx(1), q=await c2.newPage();
  q.on('pageerror',e=>errors.push(e.message));
  await q.goto(`${live}/loom/dev/field6.html`,{waitUntil:'load'});
  await q.waitForTimeout(4500);
  const feed=await q.evaluate(()=>({repo:S&&S.repo,tick:S&&S.shuttle&&S.shuttle.tick}));
  if(feed.tick==null){
    receipts.sweep={skipped:'no live feed reachable — the tick could not be read'};
  }else{
    await q.evaluate(()=>{ACTOR.camera.jump(0.46);}); await q.waitForTimeout(1500);
    const n0=await q.evaluate(()=>BEAT.n);
    await q.waitForFunction(n=>BEAT.n>n,n0,{timeout:45000});   // the daemon's own beat
    await q.waitForTimeout(260);                               // the ring, still in flight
    await q.screenshot({path:resolve(out,'sweep-ring.png')});
    const hub0=await q.evaluate(()=>({x:Math.round(hub.x),y:Math.round(waterAt(CAM.z))}));
    await png2(q,'sweep-hub', await cutOn(q,hub0.x-190,hub0.y-230,380,250,2));
    const got=await q.evaluate(()=>({
      tick:BEAT.tick,n:BEAT.n,period_ms:Math.round(BEAT.period||0),
      echoes:hub.cells.filter(c=>c.echo).map(c=>({room:c.k,...c.echo,t0:undefined,cell:undefined}))}));
    receipts.sweep={feed:feed.repo,...got};
    await q.waitForTimeout(1400);
    await q.screenshot({path:resolve(out,'sweep-echoes.png')});
  }
  await c2.close();
}

// ── 1 · frame cost, the pass on and off, at both densities ───────────────────
// The honest number is the frame-to-frame delta the loop measures itself; the
// stage sub-timers under-report because canvas work is deferred past them.
receipts.frame_ms = [];
{
  for (const dsf of [1,2]) {
    const c = await ctx(dsf), p = await c.newPage();
    p.on('pageerror',e=>errors.push('frame:'+e.message));
    for (const q of ['fx=off','']) {
      await p.goto(page6(q),{waitUntil:'load'}); await settle(p);
      const s=[]; for(let i=0;i<5;i++){await p.waitForTimeout(1000);s.push(await p.evaluate(()=>window.__fx));}
      const avg=k=>+(s.reduce((a,v)=>a+v[k],0)/s.length).toFixed(3);
      receipts.frame_ms.push({px:`${1440*dsf}x${900*dsf}`,case:q||'full pass',
        fps_min:Math.min(...s.map(v=>v.fps)),frameMs:avg('frameMs'),
        worldMs:avg('worldMs'),passMs:avg('passMs')});
    }
    // the cost of this block alone, at the same scene state: the world timer
    // with RECORD.draw live, then with it stubbed out, nothing else touched.
    const p2 = await c.newPage();
    p2.on('pageerror',e=>errors.push('cost:'+e.message));
    await p2.goto(page6(),{waitUntil:'load'}); await settle(p2);
    const grab=async()=>{const s=[];for(let i=0;i<4;i++){await p2.waitForTimeout(800);
      s.push(await p2.evaluate(()=>window.__fx));}
      return {frameMs:+(s.reduce((a,v)=>a+v.frameMs,0)/s.length).toFixed(3),
              worldMs:+(s.reduce((a,v)=>a+v.worldMs,0)/s.length).toFixed(3)};};
    const withR = await grab();
    // (repaired by the layer pass) this stubbed `RECORD.draw`, which no longer
    // exists — the block has four seams now, one per layer it lands in — so the
    // stub was a no-op and the delta it reported was measurement noise, twice
    // NEGATIVE on the run that caught it. It stubs all four.
    await p2.evaluate(()=>{window.__rd={};
      for(const k of ['ground','marks','water','prepare']){window.__rd[k]=RECORD[k];RECORD[k]=()=>{};}
      window.__rw={wire:RECORD.wireWindow,pane:RECORD.paneWindow,hover:RECORD.hoverWindow};
      RECORD.wireWindow=()=>null;RECORD.paneWindow=()=>null;RECORD.hoverWindow=()=>null;});
    const withoutR = await grab();
    await p2.evaluate(()=>{for(const k in window.__rd)RECORD[k]=window.__rd[k];
      RECORD.wireWindow=window.__rw.wire;RECORD.paneWindow=window.__rw.pane;
      RECORD.hoverWindow=window.__rw.hover;});
    receipts[`record_cost_${dsf}x`]={with:withR,without:withoutR,
      deltaWorldMs:+(withR.worldMs-withoutR.worldMs).toFixed(3),
      deltaFrameMs:+(withR.frameMs-withoutR.frameMs).toFixed(3)};
    await c.close();
  }
}

// ── 2 · the cloth, and the fold in both directions, by hand ──────────────────
{
  const c = await ctx(1), p = await c.newPage();
  p.on('pageerror',e=>errors.push('fold:'+e.message));
  await p.goto(page6(),{waitUntil:'load'}); await settle(p);

  receipts.cloth = await p.evaluate(()=>({
    passes:RECORD.passes.length,
    live:RECORD.passes.filter(q=>q.live).map(q=>q.run),
    span:[RECORD.passes[0].started,RECORD.passes.at(-1).started],
    withTokens:RECORD.passes.filter(q=>typeof q.tokens==='number').length,
    withTopics:RECORD.passes.filter(q=>(q.topics||[]).length).length,
    decks:[...RECORD.tints.keys()],
    footprintRuns:RECORD.fpByRun.size,
    footprintRunsOnCloth:[...RECORD.fpByRun.keys()].filter(r=>RECORD.byRun.has(r)).length,
    body:RECORD.body}));
  await shot(p,'01-cloth');

  // to the schematic, where the rooms are rooms — by the real key
  await p.keyboard.press('3'); await p.waitForTimeout(2600);  // > the phosphor's ~1.5 s ghost

  // ── the fold, room → pass. A real click on a room with footprints. ──
  const pickRoom = async id => p.evaluate(rid=>{
    const r=byItem.get(rid); if(!r) return null;
    const q=roomXY(r,CAM.z); return {x:Math.round(q.x),y:Math.round(q.y),
      fp:(r.it.footprints||[]).length,visited:r.it.visited_at||null,title:r.it.title};
  }, id);
  const busiest = await p.evaluate(()=>{
    const its=(S.warp.items||[]).filter(i=>(i.footprints||[]).length);
    its.sort((a,b)=>b.footprints.length-a.footprints.length);
    return its[0].id;});
  const rc = await pickRoom(busiest);
  await p.mouse.click(rc.x,rc.y); await p.waitForTimeout(1800);
  receipts.fold_room_to_pass = await p.evaluate(()=>{
    const s=RECORD.selection();
    const m=s&&s.kind==='room'?RECORD.passesOf(s.id):new Map();
    return {selected:s, passesLit:[...m.keys()], footprints:[...m.values()].reduce((a,b)=>a+b,0),
            allOnCloth:[...m.keys()].every(r=>RECORD.byRun.has(r))};});
  await shot(p,'02-fold-room-to-pass');
  assert.ok(receipts.fold_room_to_pass.passesLit.length>0,'a room with footprints must light passes');
  assert.ok(receipts.fold_room_to_pass.allOnCloth,'every lit pass must be a plaque on the cloth');

  // ── a room with NO footprints must say so, not read as unvisited-by-choice ──
  const bare = await p.evaluate(()=>{
    const it=(S.warp.items||[]).find(i=>!(i.footprints||[]).length);
    return it?it.id:null;});
  const bc = await pickRoom(bare);
  await p.mouse.click(bc.x,bc.y); await p.waitForTimeout(1800);
  receipts.fold_empty_room = {id:bare, ...await p.evaluate(()=>{
    const s=RECORD.selection();
    return {selected:s, passesLit:RECORD.passesOf(s.id).size,
            visited:(byItem.get(s.id)||{it:{}}).it.visited_at||null};})};
  await shot(p,'03-fold-room-with-no-footprints');
  assert.equal(receipts.fold_empty_room.passesLit,0);

  // ── the fold, pass → room. A real click on a plaque. ──
  const plaque = await p.evaluate(()=>{
    // the pass with the most rooms, so the outward direction has something to show
    let best=null,n=-1;
    for(const [run,m] of RECORD.fpByRun) if(m.size>n){n=m.size;best=run;}
    const i=RECORD.passes.findIndex(q=>q.run===best);
    const G=RECORD.geom;
    return {run:best,rooms:n,i,x:Math.round(G.x0+(i+0.5)*G.pw),y:Math.round(G.base-6)};});
  await p.mouse.move(plaque.x,plaque.y); await p.waitForTimeout(300);
  await shot(p,'04-plaque-hover');
  const plaqueUnder = await p.evaluate(a=>{const h=RECORD.hit(a.x,a.y);return h?h.run:null;},plaque);
  assert.equal(plaqueUnder,plaque.run,'RECORD.hit must find the plaque the cursor is over');
  await p.mouse.click(plaque.x,plaque.y); await p.waitForTimeout(1800);
  receipts.fold_pass_to_room = {clicked:plaque, ...await p.evaluate(()=>({
    pass:RECORD.pass, rooms:[...RECORD.roomsOf(RECORD.pass).keys()],
    footprints:[...RECORD.roomsOf(RECORD.pass).values()].reduce((a,b)=>a+b,0),
    allInWarp:[...RECORD.roomsOf(RECORD.pass).keys()].every(id=>byItem.has(id))}))};
  await shot(p,'05-fold-pass-to-room');
  assert.equal(receipts.fold_pass_to_room.pass,plaque.run);
  assert.ok(receipts.fold_pass_to_room.rooms.length>0,'a pass with footprints must light rooms');
  assert.ok(receipts.fold_pass_to_room.allInWarp,'every lit room must exist in the warp');

  // ── the scrubber: arrow keys walk the cloth ──
  const before = await p.evaluate(()=>RECORD.cursor);
  await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(250);
  await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(250);
  receipts.scrub = {before, after: await p.evaluate(()=>({cursor:RECORD.cursor,pass:RECORD.pass}))};
  assert.equal(receipts.scrub.after.cursor, before-2, 'two ArrowLefts move the cursor two passes');

  // ── the window's second user: a pass, stopped and inspected ──
  await p.keyboard.press('Enter'); await p.waitForTimeout(800);
  receipts.pass_window = await p.evaluate(()=>({win:RECORD.win,pass:RECORD.pass}));
  await shot(p,'08-the-pass-window');
  assert.equal(receipts.pass_window.win,'pass','Enter on a selected pass must inspect it');
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);
  receipts.esc_closes_window_first = await p.evaluate(()=>({win:RECORD.win,pass:RECORD.pass}));
  assert.equal(receipts.esc_closes_window_first.win,null);
  assert.ok(receipts.esc_closes_window_first.pass,'esc closes the window before it clears the selection');

  // ── the seam: an [ACTOR] that writes SELECTED lights the cloth with no
  //    local mirror in play. This is the merge, rehearsed from one branch. ──
  // (repaired by the layer pass) this drove the seam by ASSIGNING SELECTED,
  // which the merge made impossible — [ACTOR] declares it `const`, so the
  // assignment threw and took the whole harness with it. It goes through
  // [ACTOR]'s own select() now, which is what this file says it does anyway:
  // every direction of the fold driven by the real interaction.
  receipts.seam = await p.evaluate(rid=>{
    RECORD.pass=null; RECORD.roomLocal=null;
    ACTOR.select.room(byItem.get(rid));
    const s=RECORD.selection();
    const via={selection:s, lit:[...RECORD.passesOf(rid).keys()].length, local:RECORD.roomLocal};
    const dk=decks.find(d=>d.slug==='the-loom');          // the third direction
    if(dk)ACTOR.select.deck(dk);
    const deck={selection:RECORD.selection(), passes:RECORD.passesOfDeck('the-loom').size};
    ACTOR.select.clear();
    return {via,deck};
  }, busiest);
  assert.equal(receipts.seam.via.local,null,'SELECTED must be honoured without the local mirror');
  assert.ok(receipts.seam.via.lit>0);

  // The occlusion test is retired with the thing it tested: after the fourth
  // steer this block draws no panel at all, so there is nothing to occlude.
  // What replaces it is the altitude scope — the chat must be INVISIBLE at the
  // schematic, which is the rule that made the panel unnecessary.
  {
    const q = await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
    const pp = await q.newPage();
    pp.on('pageerror',e=>errors.push('scope:'+e.message));
    await pp.goto(page6('fx=off'),{waitUntil:'load'}); await settle(pp);
    const probe = async () => pp.evaluate(()=>({z:+CAM.z.toFixed(3),
      chatVisible: RECORD.wireTop!==null && (function(){
        const g=document.getElementById('c').getContext('2d');
        const d=g.getImageData(Math.round(innerWidth*0.04),Math.round(RECORD.wireTop)-10,
                               Math.round(innerWidth*0.45),44).data;
        let mx=0; for(let i=0;i<d.length;i+=4){
          const v=0.2126*d[i]+0.7152*d[i+1]+0.0722*d[i+2]; if(v>mx)mx=v;}
        return Math.round(mx);})(),
      rootDepthNonZero: RECORD.roots.length>0}));
    await pp.keyboard.press('1'); await pp.waitForTimeout(2600);
    const interior = await probe();
    await pp.screenshot({path:resolve(out,'10-interior-roots-and-the-chat.png')});
    await pp.keyboard.press('3'); await pp.waitForTimeout(2600);
    const schematic = await probe();
    await pp.screenshot({path:resolve(out,'11-schematic-the-band-is-the-warps.png')});
    receipts.altitude_scope={interior,schematic};
    await q.close();
    assert.ok(interior.chatVisible > 60, `the chat must read at the interior (${interior.chatVisible})`);
    assert.ok(schematic.chatVisible < interior.chatVisible*0.5,
      `the chat must give the band back at the schematic (${schematic.chatVisible} vs ${interior.chatVisible})`);
  }

  // ── the body, and the wire, on their own keys ──
  await p.evaluate(()=>{ACTOR.select.clear();RECORD.pass=null;RECORD.roomLocal=null;});
  await p.keyboard.press('1'); await p.waitForTimeout(2600);  // > the phosphor's ~1.5 s ghost   // back to the interior
  await p.keyboard.press('p'); await p.waitForTimeout(900);
  receipts.body = await p.evaluate(()=>{
    const b=S.beads, base=(b[0].ctx_after||0)-(b[0].delta||0);
    const sum=b.reduce((a,x)=>a+Math.max(0,x.delta||0),0);
    return {window:RECORD.win, occ:RECORD.body.occ, win:RECORD.body.win,
      say:RECORD.body.say, base, sum, stacked:base+sum,
      reconciles:Math.abs(base+sum-RECORD.body.occ)<1, feedHasPack:('pack' in S)};});
  await shot(p,'06-the-body-window');
  assert.equal(receipts.body.window,'body','P must open the body window');
  assert.equal(receipts.body.reconciles,true,'the transcript column must reconcile with the feed');
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  await p.keyboard.press('l'); await p.waitForTimeout(900);
  receipts.wire = await p.evaluate(()=>({log:RECORD.log,lines:RECORD.wire.length,
    channels:RECORD.chanCount,
    placed:RECORD.wire.filter(l=>l.place).length,
    streamHasNoWeights:RECORD.wire.every(l=>l.base===undefined),
    tail:RECORD.wire.slice(-3).map(l=>({who:l.who,chan:l.chan,place:l.place}))}));
  await shot(p,'07-the-history-is-the-body');
  // NOT an assertion: the feed's inbound window rotates, so his line is present
  // some minutes and gone others. It was measured present (1 of 4) at 20:35Z and
  // absent at 20:47Z. A test that depends on a transient feed row is a bad test;
  // the count is the receipt.
  assert.equal(receipts.wire.streamHasNoWeights,true,'a stream carries no inspection');
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);

  // ── the tint: one bend per deck, and a ping that carries its own ──
  receipts.tint = await p.evaluate(()=>{
    const out={};
    for(const t of RECORD.tints.keys())out[t]={offset:+(RECORD.hue(t)-38.3).toFixed(1),
      ink:RECORD.tint([t],1)};
    out['(no deck measured)']={offset:0,ink:RECORD.tint([],1)};
    // fire one ping per deck from the pod, plainly a harness act
    const pod=pods.find(q=>q.kind==='resident'), o={x:pod.x,y:pod.y};
    [...RECORD.tints.keys()].slice(0,5).forEach((t,i)=>{
      const n=nodes[(i*37)%nodes.length];
      pings.push({u:o.x/W,v:(o.y-waterAt(CAM.z))/H,aim:{node:n},room:null,
        born:performance.now()+i*40,act:'(harness)',kind:'file',detail:'tint '+t,topics:[t]});});
    return out;});
  await p.waitForTimeout(520);
  await shot(p,'09-tint-one-ping-per-deck');
  await p.waitForTimeout(1400);
  await shot(p,'09b-tint-after-the-ghost');
  await c.close();
}

// ── 10 · THE LAYER PASS ─────────────────────────────────────────────────────
// Four layers, one compositor. Every receipt below is a number the page itself
// publishes or a position sampled from the real animation — never a look.
// `LAYERS_BEFORE` points at a copy of the page as it stood before this pass;
// unset, the before/after legs are skipped and say so.
const before = process.env.LAYERS_BEFORE || null;   // e.g. /loom/dev/field6-before.html
const pageAt = (u,q) => `${base}${u}${q?'?'+q:''}`;
const cutOf = (p,x0,y0,w,h,s=1) => p.evaluate(([x0,y0,w,h,s])=>{
  const c=document.createElement('canvas');c.width=w*s;c.height=h*s;
  const g=c.getContext('2d');g.imageSmoothingEnabled=false;
  g.drawImage(document.getElementById('c'),
    x0*devicePixelRatio,y0*devicePixelRatio,w*devicePixelRatio,h*devicePixelRatio,0,0,w*s,h*s);
  return c.toDataURL('image/png');},[x0,y0,w,h,s]);
const pngOf = async (n,d) => writeFile(resolve(out,n+'.png'),Buffer.from(d.split(',')[1],'base64'));

// 10a · the actor's load. Sampled from the first frame that has a target, so
// the receipt is the path itself: how far the pod travelled from the position
// it first asserted to the one it actually held.
{
  const legs=[['after','/loom/dev/field6.html']].concat(before?[['before',before]]:[]);
  for (const [name,url] of legs) {
    const c=await ctx(1), p=await c.newPage();
    p.on('pageerror',e=>errors.push('load:'+name+':'+e.message));
    await p.goto(pageAt(url),{waitUntil:'load'});
    const track=await p.evaluate(async()=>{
      const seen=[];
      for(let i=0;i<70&&seen.length<=28;i++){
        await new Promise(r=>setTimeout(r,100));
        const q=(typeof pods!=='undefined')&&pods.find(x=>x.kind==='resident');
        if(q&&q.target)seen.push([Math.round(q.x),Math.round(q.y)]);
      }
      return seen;});
    const last=track[track.length-1]||[0,0];
    receipts['load_'+name]={firstFrameWithATarget:track[0]||null,settled:last,samples:track.length,
      travelledFromItsFirstReading:Math.round(track.reduce((m,[x,y])=>
        Math.max(m,Math.hypot(x-last[0],y-last[1])),0))};
    await p.waitForTimeout(600);
    await pngOf('layers-load-'+name, await cutOf(p,120,440,620,300,2));
    await c.close();
  }
  assert.equal(receipts.load_after.travelledFromItsFirstReading,0,
    'the first reading is a placement: a pod must not fly in from a position it never held');
}

// 10b · a room card over the cloth — the defect this pass was dispatched for
{
  const legs=[['after','/loom/dev/field6.html']].concat(before?[['before',before]]:[]);
  for (const [name,url] of legs) {
    const c=await ctx(1), p=await c.newPage();
    p.on('pageerror',e=>errors.push('card:'+name+':'+e.message));
    await p.goto(pageAt(url),{waitUntil:'load'}); await settle(p);
    await p.keyboard.press('3'); await p.waitForTimeout(2600);
    const busiest=await p.evaluate(()=>{
      const its=(S.warp.items||[]).filter(i=>(i.footprints||[]).length);
      its.sort((a,b)=>b.footprints.length-a.footprints.length);return its[0].id;});
    const at=await p.evaluate(id=>{const r=byItem.get(id),q=roomXY(r,CAM.z);
      return {x:Math.round(q.x),y:Math.round(q.y)};},busiest);
    await p.mouse.click(at.x,at.y); await p.waitForTimeout(2200);
    receipts['card_'+name]={item:busiest,...await p.evaluate(()=>{
      const G=RECORD.geom, cd=(typeof RESERVE!=='undefined'&&RESERVE.card)||null;
      return {clothRightEdge:Math.round(G.x1),plaqueWidth:+G.pw.toFixed(2),
        cardLeftEdge:cd?Math.round(cd.x):null,
        clothOverCard:Math.max(0,Math.round(G.x1-(cd?cd.x:innerWidth-376)))};})};
    await pngOf('layers-card-'+name, await cutOf(p,1000,620,440,280,2));
    await shot(p,'layers-card-full-'+name);
    await c.close();
  }
  assert.equal(receipts.card_after.clothOverCard,0,
    'where a stream and a window want the same pixels, the stream is the one that moves');
}

// 10c · the compositor's own receipt: nothing draws outside a layer
{
  const c=await ctx(1), p=await c.newPage();
  p.on('pageerror',e=>errors.push('layers:'+e.message));
  await p.goto(pageAt('/loom/dev/field6.html','layers=check'),{waitUntil:'load'});
  // the glitch belongs to arrival: the whole tree arrives at once on first load
  await p.waitForTimeout(900);
  receipts.arrival=await p.evaluate(()=>({
    glitching:nodes.filter(n=>glitchK(n.born,performance.now())!==null).length,of:nodes.length}));
  await shot(p,'layers-arrival');
  await settle(p);
  receipts.arrival.stillGlitchingAfterSettle=await p.evaluate(()=>
    nodes.filter(n=>glitchK(n.born,performance.now())!==null).length);
  assert.equal(receipts.arrival.stillGlitchingAfterSettle,0,'a glitch is an arrival, never a state');
  await p.keyboard.press('3'); await p.waitForTimeout(2200);
  receipts.layers_idle=await p.evaluate(()=>window.__layers);
  await p.keyboard.press('l'); await p.waitForTimeout(900);
  receipts.layers_transcript=await p.evaluate(()=>window.__layers);
  await shot(p,'layers-transcript-is-a-window');
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);
  await p.keyboard.press('p'); await p.waitForTimeout(900);
  receipts.layers_body=await p.evaluate(()=>window.__layers);
  await shot(p,'layers-body-is-a-window');
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);
  for(const k of ['layers_idle','layers_transcript','layers_body']){
    assert.equal(receipts[k].stray,0,`${k}: nothing may draw outside a layer`);
    assert.equal(receipts[k].leaks,0,`${k}: no layer may leak a save()`);
  }

  // 10d · the pod travels the structure. Driven by a real target change, then
  // sampled off the real animation. The receipt is STRUCTURAL, not a magnitude:
  // the route's waypoints name a shore, and the path measurably rides it. (A
  // length ratio was the first thing measured here and it is a bad test — a
  // target that happens to sit at the shore's own height makes the routed path
  // 0.5 % longer than the straight line while being entirely correct.)
  await p.keyboard.press('1'); await p.waitForTimeout(2200);
  receipts.route=await p.evaluate(async()=>{
    const me=pods.find(q=>q.kind==='resident');
    // the highest place on the furthest bough: a straight line to it cuts
    // diagonally across the whole canopy, which is the move being ended
    const far=nodes.slice().sort((a,b)=>
      (Math.abs(b.x-me.x)+b.e*260)-(Math.abs(a.x-me.x)+a.e*260))[0];
    const rt=routeBetween(me.routeTo,{node:far},me)||[];
    const from={x:me.x,y:me.y};
    me.target={node:far};
    const seen=[];
    for(let i=0;i<150;i++){await new Promise(r=>requestAnimationFrame(r));
      seen.push([me.x,me.y]);
      if(i>2&&me.u>=1&&!me.route)break;}
    const to=seen[seen.length-1];
    let walked=0;for(let i=1;i<seen.length;i++)walked+=Math.hypot(seen[i][0]-seen[i-1][0],seen[i][1]-seen[i-1][1]);
    // how far the path bulges off the straight line it refused to take
    const dx=to[0]-from.x, dy=to[1]-from.y, len=Math.hypot(dx,dy)||1;
    const dev=Math.max(...seen.map(([x,y])=>Math.abs((x-from.x)*dy-(y-from.y)*dx)/len));
    const shore=waterAt(CAM.z)-10-14*CAM.z;
    return {waypoints:rt.map(a=>Object.keys(a)[0]),
      straightLine:Math.round(len),walked:Math.round(walked),
      maxDeviationFromTheStraightLine:Math.round(dev),
      closestApproachToTheShore:Math.round(Math.min(...seen.map(([,y])=>Math.abs(y-shore)))),
      frames:seen.length};});
  assert.ok(receipts.route.waypoints.includes('shore'),
    'a route between two places on the island goes by way of the shore');
  assert.ok(receipts.route.closestApproachToTheShore<10,
    'and the pod measurably rides it, rather than being routed on paper');
  assert.ok(receipts.route.maxDeviationFromTheStraightLine>20,
    'a path that never leaves the chord is the straight line by another name');
  await shot(p,'layers-route');

  receipts.beat=await p.evaluate(()=>({tick:BEAT.tick,sightings:BEAT.n,
    period_ms:BEAT.period&&Math.round(BEAT.period),src:BEAT.src,rejected:BEAT.rejected,
    breathNow:+breath(performance.now()).toFixed(3),amplitude:BREATH_A}));
  await c.close();
}

await browser.close();
assert.deepEqual(errors,[],'the page must raise no errors');
console.log(JSON.stringify({receipts,out},null,1));
