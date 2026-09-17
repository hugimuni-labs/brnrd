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
const base = process.env.LOOM_URL || 'http://127.0.0.1:7796';
const out  = process.env.OUT || '/tmp/recordshots';
const page6 = q => `${base}/loom/dev/field6.html${q?'?'+q:''}`;
await mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const errors=[], receipts={};
const ctx = dsf => browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:dsf});
const settle = p => p.waitForTimeout(4200);
const shot = (p,n) => p.screenshot({path:resolve(out,n+'.png')});

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
    await p2.evaluate(()=>{window.__rd=RECORD.draw;RECORD.draw=()=>{};});
    const withoutR = await grab();
    await p2.evaluate(()=>{RECORD.draw=window.__rd;});
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
  receipts.seam = await p.evaluate(rid=>{
    RECORD.pass=null; RECORD.roomLocal=null;
    SELECTED={kind:'room',id:rid};
    const s=RECORD.selection();
    const via={selection:s, lit:[...RECORD.passesOf(rid).keys()].length, local:RECORD.roomLocal};
    SELECTED={kind:'deck',id:'the-loom'};                 // the third direction
    const deck={selection:RECORD.selection(), passes:RECORD.passesOfDeck('the-loom').size};
    SELECTED=null;
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
  await p.evaluate(()=>{SELECTED=null;RECORD.pass=null;RECORD.roomLocal=null;});
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
  // ── P follows the selection: the seat, then a strand's budget ──
  await p.keyboard.press('p'); await p.waitForTimeout(900);
  receipts.strand_window = await p.evaluate(()=>{
    const id=RECORD.podLocal, st=(S.hud.strands||[]).find(q=>q.id===id);
    return {win:RECORD.win,pod:id,
      title:st&&st.title, spent:st&&st.spent, allowance:st&&st.allowance,
      status:st&&st.status, places:st&&(st.places||[]).length,
      strandsInFeed:(S.hud.strands||[]).length};
  });
  await shot(p,'12-a-strands-body-is-a-budget');
  assert.ok(receipts.strand_window.pod,'P again must walk to a strand while [ACTOR] has no pod pick');
  assert.ok(receipts.strand_window.allowance>0,'a strand panel needs a real end to draw');
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
await browser.close();
assert.deepEqual(errors,[],'the page must raise no errors');
console.log(JSON.stringify({receipts,out},null,1));
