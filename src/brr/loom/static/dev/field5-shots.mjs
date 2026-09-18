// Visual rehearsal for field5's [FRAME] post-pass. Inspect every saved image.
//
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs> \
//   LOOM_URL=http://127.0.0.1:7791 OUT=/tmp/podshots node field5-shots.mjs
//
// Headless chromium on this machine falls back to SwiftShader, where every
// full-screen composite costs ~12 ms and the pass reads as a slideshow at 15 fps.
// That is the renderer, not the pass: launch with a real GPU (below) or the
// numbers are fiction. `headless:false` + --use-angle=metal was what measured.
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE
  || '/Users/gurio/Source/Projects/brnrd/src/frontend/node_modules/playwright/index.mjs'));
const base = process.env.LOOM_URL || 'http://127.0.0.1:7791';
const out  = process.env.OUT || '/tmp/podshots';
const page5 = q => `${base}/loom/dev/field5.html${q?'?'+q:''}`;
await mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const errors=[], receipts={};
const ctx = async dsf => browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:dsf});
const settle = p => p.waitForTimeout(4500);

// ── 1 · frame time, with the pass and without ────────────────────────────────
// The honest number is the frame-to-frame delta, not the pass sub-timer: canvas
// work is deferred past the timed span, so the stages under-report and only the
// total moves. ?sync=1 forces a 1x1 readback per frame to flush the pipeline —
// useful for ordering the stages, far too stall-dominated to quote as a cost.
receipts.frame_ms = [];
for (const dsf of [1,2]) {
  const c = await ctx(dsf), p = await c.newPage();
  p.on('pageerror',e=>errors.push(e.message));
  for (const q of ['fx=off','','bloom=filter']) {
    await p.goto(page5(q),{waitUntil:'load'}); await settle(p);
    const s=[]; for(let i=0;i<6;i++){await p.waitForTimeout(1000);s.push(await p.evaluate(()=>window.__fx));}
    const avg=k=>+(s.reduce((a,v)=>a+v[k],0)/s.length).toFixed(3);
    receipts.frame_ms.push({px:`${1440*dsf}x${900*dsf}`,case:q||'full pass',
      fps_min:Math.min(...s.map(v=>v.fps)), frameMs:avg('frameMs'), passMs:avg('passMs')});
  }
  await c.close();
}

// ── 2 · the before/after pair, and the ping still ────────────────────────────
// One page, one scene state, the pass flipped live: two page loads cannot make
// this comparison because the world moves between them.
{
  const c = await ctx(1), p = await c.newPage();
  p.on('pageerror',e=>errors.push(e.message));
  await p.goto(page5(),{waitUntil:'load'}); await settle(p);
  const fired = await p.evaluate(()=>{                 // harness act, plainly labelled
    const pod=pods[0], cand=nodes.filter(n=>!n.isLabel);
    let to=cand[0],best=-1; for(const n of cand){const d=Math.hypot(n.x-pod.x,n.y-pod.y);if(d>best){best=d;to=n;}}
    pings.push({x:pod.x,y:pod.y,to,born:performance.now(),act:'(harness)',kind:'file',detail:'pair'});
    return {to:to.path, dist:Math.round(best)};
  });
  await p.waitForTimeout(620);                          // caught mid-flight
  await p.screenshot({path:resolve(out,'after-fx-on.png')});
  await p.evaluate(()=>__fxSet(false)); await p.waitForTimeout(90);
  await p.screenshot({path:resolve(out,'before-fx-off.png')});
  receipts.pair = fired;
  await c.close();
}

// ── 3 · the ghost trail, twelve frames ───────────────────────────────────────
{
  const c = await ctx(1), p = await c.newPage();
  p.on('pageerror',e=>errors.push(e.message));
  await p.goto(page5(),{waitUntil:'load'}); await settle(p);
  const res = await p.evaluate(async ()=>{
    const pod=pods[0], cand=nodes.filter(n=>!n.isLabel);
    let to=cand[0],best=-1; for(const n of cand){const d=Math.hypot(n.x-pod.x,n.y-pod.y);if(d>best){best=d;to=n;}}
    const x0=Math.round(Math.min(pod.x,to.x)-70), y0=Math.round(Math.min(pod.y,to.y)-70);
    const w=Math.round(Math.abs(to.x-pod.x)+140), h=Math.round(Math.abs(to.y-pod.y)+140);
    const cut=document.createElement('canvas');cut.width=w;cut.height=h;const g=cut.getContext('2d');
    pings.push({x:pod.x,y:pod.y,to,born:performance.now(),act:'(harness)',kind:'file',detail:'sheet'});
    const t0=performance.now(), frames=[];
    for(let i=0;i<12;i++){
      await new Promise(r=>setTimeout(r,i?130:40));
      g.clearRect(0,0,w,h);
      g.drawImage(document.getElementById('c'),x0*devicePixelRatio,y0*devicePixelRatio,
        w*devicePixelRatio,h*devicePixelRatio,0,0,w,h);
      frames.push({t:Math.round(performance.now()-t0),d:cut.toDataURL('image/png')});
    }
    return {w,h,frames};
  });
  for(const [i,f] of res.frames.entries())
    await writeFile(resolve(out,`sheet-${String(i).padStart(2,'0')}.png`),Buffer.from(f.d.split(',')[1],'base64'));
  receipts.sheet_ms = res.frames.map(f=>f.t);
  // the ghost is gone by ~1.5 s, which is the whole claim of the decay
  assert.ok(res.frames.at(-1).t > 1500, 'the sheet must outlast the ghost');
  await c.close();
}

// ── 4 · the two discipline probes ────────────────────────────────────────────
{
  const c = await ctx(1), p = await c.newPage();
  p.on('pageerror',e=>errors.push(e.message));
  const lum = async q => { await p.goto(page5(q),{waitUntil:'load'}); await settle(p);
    return p.evaluate(()=>{const g=document.getElementById('c').getContext('2d');
      const rd=(x,y,w,h)=>{const d=g.getImageData(x,y,w,h).data;let s=0,mx=0,n=0;
        for(let i=0;i<d.length;i+=4){const l=0.2126*d[i]+0.7152*d[i+1]+0.0722*d[i+2];s+=l;if(l>mx)mx=l;n++;}
        return {mean:+(s/n).toFixed(2),max:Math.round(mx)};};
      return {empty:rd(1050,150,320,220), whole:rd(0,0,1440,900)};});};
  receipts.black = {off: await lum('fx=off'), on: await lum('')};
  // the dark is a reading, not a backdrop: the pass may not lift an empty region
  assert.ok(receipts.black.on.empty.mean <= receipts.black.off.empty.mean + 0.2,
    'the pass must not lift the black');

  // chromatic aberration is lateral: zero at the centre, strongest at the edge.
  // A(ab on) B(ab off) C(ab on) on consecutive frames — diff(A,C) is the motion
  // floor the aberration has to be read against.
  await p.goto(page5(),{waitUntil:'load'}); await settle(p);
  const shot = ()=>p.evaluate(()=>document.getElementById('c').toDataURL('image/png'));
  const A=await shot(); await p.evaluate(()=>__fxSet(false,'ab')); await p.waitForTimeout(34);
  const B=await shot(); await p.evaluate(()=>__fxSet(true ,'ab')); await p.waitForTimeout(34);
  const C=await shot();
  for(const [t,d] of [['A',A],['B',B],['C',C]])
    await writeFile(resolve(out,`ab-${t}.png`),Buffer.from(d.split(',')[1],'base64'));
  receipts.ab_note='radius profile of |A-B| is computed by the report script; centre ~0, edge ~0.39';
  await c.close();
}
await browser.close();
assert.deepEqual(errors,[]);
console.log(JSON.stringify({receipts,out},null,1));
