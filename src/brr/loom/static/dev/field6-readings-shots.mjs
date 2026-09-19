// Same captured live feed and camera for all three before/after readings.
// FEED=state.json BEFORE_HTML=field6-before.html BEFORE_PAGES=http://127.0.0.1:7796
// PAGES=http://127.0.0.1:7798 PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs OUT=/tmp/readings node this-file
// BEFORE_PAGES serves the baseline pages.py; PAGES serves this checkout.
import {fileURLToPath,pathToFileURL} from 'node:url';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE));
const out=process.env.OUT||'/tmp/readings';await mkdir(out,{recursive:true});
const feed=JSON.parse(await readFile(process.env.FEED,'utf8'));
const html=await readFile(fileURLToPath(new URL('./field6.html',import.meta.url)),'utf8');
const before=await readFile(process.env.BEFORE_HTML,'utf8');
const servers=[];
async function serve(document,pages){
  const server=createServer(async(req,res)=>{
    try{
      if(req.url.startsWith('/loom/page/')){
        const r=await fetch(pages+req.url);res.writeHead(r.status,{'Content-Type':'application/json'});res.end(await r.text());return;
      }
      res.setHeader('Content-Type',req.url.includes('state.json')?'application/json':'text/html');
      res.end(req.url.includes('state.json')?JSON.stringify(feed):document);
    }catch(e){res.writeHead(502);res.end('{}');}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));servers.push(server);
  return `http://127.0.0.1:${server.address().port}`;
}
const urls={before:await serve(before,process.env.BEFORE_PAGES),after:await serve(html,process.env.PAGES)};
const browser=await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const context=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
const p=await context.newPage(),errors=[],requests=[];p.on('pageerror',e=>errors.push(e.stack));
p.on('request',r=>{if(r.url().includes('/loom/page/place'))requests.push(r.url());});
const receipt={feedAt:feed.at,run:feed.run.id,views:{}};
const pause=()=>p.waitForTimeout(800);
const pass=process.env.PASS||'run-260918-0009-enh9';
async function screenshot(name){await pause();await p.screenshot({path:`${out}/${name}.png`});}
try{
  for(const phase of ['before','after']){
    await p.goto(urls[phase]);await p.waitForFunction(()=>S&&RECORD.geom);await pause();
    const gpu=await p.evaluate(()=>{const gl=document.createElement('canvas').getContext('webgl');
      const e=gl?.getExtension('WEBGL_debug_renderer_info');return e&&gl.getParameter(e.UNMASKED_RENDERER_WEBGL);});
    assert.match(gpu,/Metal/);receipt.gpu=gpu;
    const camera=await p.evaluate(()=>CAM.z);assert.equal(camera,1);
    // Canvas text is captured only for correctness, never during FPS timing.
    await p.evaluate(()=>{window.drawn=new Set();window.originalText=CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText=function(t,...args){drawn.add(String(t));return originalText.call(this,t,...args);};chromeKey='';});
    await screenshot(`${phase}-1-activity`);
    receipt.views[phase]={camera,activity:await p.evaluate(()=>typeof activityReading==='function'?activityReading(S):hudModel().rows),
      places:await p.evaluate(()=>nodes.length),weights:await p.evaluate(()=>ZOOM.rows_()?.measured)};
    await p.keyboard.press('p');await screenshot(`${phase}-2-body`);
    receipt.views[phase].body=await p.evaluate(()=>BODY);
    if(phase==='after'){
      assert.equal(receipt.views.after.body.origin,'native resume requested');
      assert.equal(receipt.views.after.body.tot,feed.beads.filter(b=>typeof b.ctx_after==='number').at(-1).ctx_after);
      assert.equal(receipt.views.after.body.splitKnown,false);
      assert.ok(await p.evaluate(()=>[...drawn].some(t=>t.includes('token split unmeasured'))));
    }
    await p.keyboard.press('Escape');
    const point=await p.evaluate(id=>{
      const i=RECORD.passes.findIndex(r=>r.run===id),g=RECORD.geom;
      if(i<0)throw new Error('captured feed lacks the requested past pass');
      return {x:g.x0+(i+.5)*g.pw,y:g.base-3};},pass);
    await p.mouse.click(point.x,point.y);await p.mouse.move(10,300);
    await p.waitForFunction(()=>ZOOM.rows_()?.measured>0);
    assert.equal(await p.evaluate(()=>RECORD.pass),pass);
    await p.evaluate(()=>{ZOOM.row='src/brr/daemon.py';ZOOM.leaf=ZOOM.row;ZOOM.win=null;ZOOM.bands=null;drawn.clear();});
    const count=requests.length;
    if(phase==='before')await p.waitForFunction(()=>ZOOM.win&&[...ZOOM.PLACE.values()].every(v=>v.state==='ok'));
    await screenshot(`${phase}-3-frozen`);
    receipt.views[phase].frozen=await p.evaluate(()=>({pass:RECORD.pass,camera:CAM.z,cache:ZOOM.PLACE.size,text:[...drawn]}));
    assert.equal(await p.evaluate(()=>CAM.z),camera);
    if(phase==='after'){
      assert.equal(requests.length,count,'past text is refused before fetch');
      assert.ok(receipt.views.after.frozen.text.includes('Historical source unavailable'));
      assert.equal(await p.evaluate(()=>breath(performance.now())),0);
    }
    await p.evaluate(()=>{CanvasRenderingContext2D.prototype.fillText=originalText;});
  }
  // Positive control: current source is reachable and labelled as current.
  await p.goto(urls.after);await p.waitForFunction(()=>S&&RECORD.geom);
  await p.evaluate(()=>{ZOOM.row='src/brr/daemon.py';ZOOM.leaf=ZOOM.row;});
  await p.waitForFunction(()=>ZOOM.PLACE.size>0&&[...ZOOM.PLACE.values()].every(v=>v.state==='ok'));
  assert.ok(await p.evaluate(()=>[...ZOOM.PLACE.values()].some(v=>v.d?.text?.text)));
  await screenshot('after-4-current-source');
  // Empty-tree, absent metadata, missing measurement, and compaction controls
  // are synthetic mutations of a captured feed, never evidence screenshots.
  receipt.controls=await p.evaluate(()=>{
    ZOOM.clearRow();const d=structuredClone(S);d.tree.repo=[];d.cloth.rows=[];d.beads=[];ingest(d);
    const empty={places:nodes.length,activity:activityReading(S)};
    const e=structuredClone(d);e.shuttle={state:'awake'};e.run.body_origin=null;
    e.beads=[{n:10,ctx_after:300,delta:0,act:'probe'},{n:11,ctx_after:100,delta:-200,act:'compact'}];
    ingest(e);RECORD.win='body';return {empty};
  });
  await pause();receipt.controls.compaction=await p.evaluate(()=>BODY);
  assert.equal(receipt.controls.empty.places,0);
  assert.match(receipt.controls.empty.activity,/waiting/);
  assert.equal(receipt.controls.compaction.tot,100);
  assert.equal(receipt.controls.compaction.run,-200);
  assert.match(receipt.controls.compaction.note,/visible tail/);
  assert.equal(receipt.controls.compaction.origin,'body origin unrecorded');
  // Return to captured evidence; no instrumentation during frame measurements.
  await p.goto(urls.after);await p.waitForFunction(()=>S&&RECORD.geom);await p.waitForTimeout(3000);
  receipt.performance=[];
  for(const z of [1,.09]){
    await p.evaluate(z=>{CAM.z=z;CAM.tz=z;},z);await p.waitForTimeout(1500);
    const samples=[];for(let i=0;i<3;i++){await p.waitForTimeout(1000);samples.push(await p.evaluate(()=>window.__fx));}
    receipt.performance.push({z,samples});
  }
  assert.deepEqual(errors,[]);receipt.errors=errors;
}finally{
  await writeFile(out+'/receipt.json',JSON.stringify(receipt,null,2));
  await browser.close();servers.forEach(s=>s.close());
}
console.log(JSON.stringify({gpu:receipt.gpu,errors,performance:receipt.performance},null,2));
