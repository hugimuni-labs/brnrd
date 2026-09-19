// Real-GPU rehearsal for [ZOOM]: the four rungs, one camera, one captured feed.
//   FEED=<captured state.json> PLAYWRIGHT_MODULE=<playwright/index.mjs>
//   PAGES=<http://127.0.0.1:PORT> OUT=<dir> node field6-zoom-shots.mjs
// The little server holds the FEED still (so every shot is the same evidence)
// and PROXIES /loom/page/* to a real daemon, because rungs 1..3 are fed by
// /loom/page/pass and /loom/page/place and a stub would prove nothing about
// them. Nothing here asserts a number the page did not compute in the browser.
import {fileURLToPath, pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE));
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import assert from 'node:assert/strict';
const out=process.env.OUT||'/tmp/zoom-shots';
const pagesBase=process.env.PAGES||'http://127.0.0.1:7799';
await mkdir(out,{recursive:true});
const html=await readFile(fileURLToPath(new URL('./field6.html',import.meta.url)),'utf8');
const feed=JSON.parse(await readFile(process.env.FEED,'utf8'));
const server=createServer(async (req,res)=>{
  if(req.url.startsWith('/loom/page/')){
    try{
      const r=await fetch(pagesBase+req.url);
      res.writeHead(r.status,{'Content-Type':'application/json'});res.end(await r.text());
    }catch(e){res.writeHead(502);res.end('{}');}
    return;
  }
  res.setHeader('Content-Type',req.url.includes('state.json')?'application/json':'text/html');
  res.end(req.url.includes('state.json')?JSON.stringify(feed):html);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const ctx=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
const p=await ctx.newPage(),errors=[];
p.on('pageerror',e=>errors.push(e.stack));
const R={};
const settle=async ms=>{await p.waitForTimeout(ms??700);};
try{
  await p.goto(base+'/field6');await settle(5000);
  assert.deepEqual(errors,[],'the page must render clean before any rung is read');
  R.gpu=await p.evaluate(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl'),
    e=g?.getExtension('WEBGL_debug_renderer_info');return e?g.getParameter(e.UNMASKED_RENDERER_WEBGL):null;});
  assert.match(R.gpu,/Metal/,'fps is only a number on the real GPU');
  const camera=await p.evaluate(()=>CAM.z);

  // ── rung 1 · the live run, as rows ─────────────────────────────────────
  R.run=await p.evaluate(()=>({rung:ZOOM.rung(),island:ZOOM.island,
    rows:(ZOOM.hits||[]).map(h=>h.path),frozen:ZOOM.frozen()}));
  assert.equal(R.run.rung,1,'the default rung is the run');
  assert.ok(R.run.rows.length>0,'positive control: the default run must draw rows');
  assert.equal(await p.evaluate(()=>nodes.length&&document.title?ZOOM.island:false),false,
    'the run rung draws no canopy');
  await p.screenshot({path:out+'/01-run.png'});

  // ── rung 0 · the island ────────────────────────────────────────────────
  const rail=i=>p.evaluate(i=>{const b=ZOOM.railBox(i);return {x:b.x+10,y:b.y+8};},i);
  let rb=await rail(0);await p.mouse.click(rb.x,rb.y);await settle(900);
  R.island=await p.evaluate(()=>({rung:ZOOM.rung(),island:ZOOM.island,nodes:nodes.length}));
  assert.equal(R.island.rung,0);
  assert.ok(R.island.nodes>0,'the island rung has the canopy back');
  await p.screenshot({path:out+'/00-island.png'});
  rb=await rail(1);await p.mouse.click(rb.x,rb.y);await settle(700);   // back to the run rung
  assert.equal(await p.evaluate(()=>ZOOM.rung()),1);

  // ── a past pass: the frozen frame ──────────────────────────────────────
  const target=process.env.PASS||feed.cloth.rows.filter(r=>r.ended&&(r.trail||[]).length).slice(-1)[0].run;
  const click=await p.evaluate(id=>{const i=RECORD.passes.findIndex(r=>r.run===id),g=RECORD.geom;
    return {x:g.x0+(i+.5)*g.pw,y:g.base-3};},target);
  await p.mouse.click(click.x,click.y);await p.mouse.move(10,300);await settle(3000);
  R.frozen=await p.evaluate(()=>({pass:RECORD.pass,rung:ZOOM.rung(),frozen:ZOOM.frozen(),
    breath:breath(performance.now()),rows:(ZOOM.hits||[]).map(h=>h.path),camera:CAM.z,
    bound:(ZOOM.bound&&ZOOM.bound())||null}));
  assert.equal(R.frozen.pass,target);
  assert.equal(R.frozen.frozen,true,'a past pass freezes the frame');
  assert.equal(R.frozen.breath,0,'a frozen frame does not breathe on the live tick');
  assert.equal(R.frozen.camera,camera,'same camera as every other shot');
  assert.ok(R.frozen.rows.length>0,'the frozen pass still draws its own rows');
  await p.screenshot({path:out+'/02-frozen-run.png'});

  // ── rung 2 · a file, its measured bands along the row ───────────────────
  const withWeight=await p.evaluate(()=>{
    const r=(ZOOM.hits||[]).map(h=>ZOOM.rowOf(h.path)).find(r=>r&&r.m&&r.m.spans.length);
    return r?r.path:((ZOOM.hits||[])[0]||{}).path;});
  const rowAt=await p.evaluate(path=>{const h=(ZOOM.hits||[]).find(h=>h.path===path);
    return {x:h.x0+40,y:(h.y0+h.y1)/2};},withWeight);
  await p.mouse.click(rowAt.x,rowAt.y);await settle(900);
  R.file=await p.evaluate(()=>({rung:ZOOM.rung(),row:ZOOM.row,
    measured:(ZOOM.rowOf(ZOOM.row)||{}).m||null}));
  assert.equal(R.file.rung,2);
  assert.equal(R.file.row,withWeight);
  await p.screenshot({path:out+'/03-file.png'});

  // ── rung 3 · the lines ─────────────────────────────────────────────────
  await p.keyboard.press('Enter');
  // A historical pass has no verified source snapshot. The leaf refuses
  // before requesting text, including against an older daemon that would
  // return today's checkout. Current text is exercised by readings-shots.
  await settle(900);
  R.lines=await p.evaluate(()=>({rung:ZOOM.rung(),leaf:ZOOM.leaf,
    frozen:ZOOM.frozen(),requests:ZOOM.PLACE.size}));
  assert.equal(R.lines.rung,3);
  assert.equal(R.lines.frozen,true);
  assert.equal(R.lines.requests,0,'historical text is refused before fetch');
  await p.screenshot({path:out+'/04-lines-unavailable.png'});

  // ── esc steps out exactly one rung, four times ─────────────────────────
  R.ladder=[];
  for(let i=0;i<4;i++){await p.keyboard.press('Escape');await settle(500);
    R.ladder.push(await p.evaluate(()=>ZOOM.rung()));}
  assert.deepEqual(R.ladder,[2,1,0,0],'esc walks the ladder down one rung at a time');

  // ── the frames, on the real GPU ────────────────────────────────────────
  R.samples=[];
  for(let i=0;i<4;i++){await settle(1000);R.samples.push(await p.evaluate(()=>window.__fx));}
  R.errors=errors;
  assert.deepEqual(errors,[],'no page error may have landed during the whole walk');
}finally{
  await writeFile(out+'/receipt.json',JSON.stringify(R,null,1));
  await browser.close();server.close();
}
console.log(JSON.stringify(R,null,1));
