// Real-GPU rehearsal: FEED=<captured state.json> PLAYWRIGHT_MODULE=<playwright/index.mjs>
// OUT=<screenshots dir> [BEFORE_HTML=<baseline.html>] node field6-tree-shots.mjs
// A captured feed keeps before/after and past selection on the same evidence.
import {fileURLToPath, pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE));
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import assert from 'node:assert/strict';
const out=process.env.OUT||'/tmp/tree-path-shots';
await mkdir(out,{recursive:true});
const html=await readFile(fileURLToPath(new URL('./field6.html',import.meta.url)),'utf8');
const before=process.env.BEFORE_HTML?await readFile(process.env.BEFORE_HTML,'utf8'):null;
const feed=JSON.parse(await readFile(process.env.FEED,'utf8'));
const server=createServer((req,res)=>{res.setHeader('Content-Type',req.url.includes('state.json')?'application/json':'text/html');res.end(req.url.includes('state.json')?JSON.stringify(feed):req.url.includes('before')?before:html);});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const ctx=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
const p=await ctx.newPage(),errors=[];
p.on('pageerror',e=>errors.push(e.stack));
const receipt={};
try{
 if(before){await p.goto(base+'/before');await p.waitForTimeout(5000);await p.screenshot({path:out+'/00-before.png'});}
 await p.goto(base+'/after');await p.waitForTimeout(5000);
 assert.deepEqual(errors,[],'page must render before assertions can pass');
 receipt.gpu=await p.evaluate(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl'),e=g?.getExtension('WEBGL_debug_renderer_info');return e?g.getParameter(e.UNMASKED_RENDERER_WEBGL):null;});
 receipt.default=await p.evaluate(()=>({scope:TREE,paths:nodes.map(n=>n.path),chunks:[...CHUNKS.keys()],camera:CAM.z,fx:window.__fx}));
 assert.match(receipt.gpu,/Metal/,'performance requires the real Metal GPU');
 assert.ok(receipt.default.paths.length>0,'positive control: default must contain paths');
 const expected=[...new Set(feed.cloth.rows.filter(r=>r.run===feed.run.id||r.parent===feed.run.id).flatMap(r=>r.trail.map(t=>t.path)))].sort();
 assert.deepEqual([...receipt.default.paths].sort(),expected);
 receipt.samples=[];
 for(let i=0;i<4;i++){await p.waitForTimeout(1000);receipt.samples.push(await p.evaluate(()=>window.__fx));}
 await p.screenshot({path:out+'/01-default.png'});
 const target=feed.cloth.rows.find(r=>r.ended&&r.trail.length&&r.run!==feed.run.id);
 const click=await p.evaluate(id=>{const i=RECORD.passes.findIndex(r=>r.run===id),g=RECORD.geom;return {x:g.x0+(i+.5)*g.pw,y:g.base-3};},target.run);
 await p.mouse.click(click.x,click.y);await p.mouse.move(10,200);await p.waitForTimeout(2500);
 receipt.past=await p.evaluate(()=>({pass:RECORD.pass,scope:TREE,paths:nodes.map(n=>n.path),chunks:[...CHUNKS.keys()],camera:CAM.z,fx:window.__fx}));
 assert.equal(receipt.past.pass,target.run);
 assert.equal(await p.evaluate(()=>SELECTED.kind),null,'cloth click must not select a deck behind it');
 assert.deepEqual([...receipt.past.paths].sort(),[...new Set(feed.cloth.rows.filter(r=>r.run===target.run||r.parent===target.run).flatMap(r=>r.trail.map(t=>t.path)))].sort());
 assert.equal(receipt.past.chunks.length,0,'past view must not borrow live chunks');
 assert.equal(receipt.past.camera,receipt.default.camera);
 await p.screenshot({path:out+'/02-past.png'});
 // Picking a historical file must not change the run being inspected.
 const n=await p.evaluate(()=>({x:nodes[0].x,y:treeY(nodes[0],CAM.z)}));await p.mouse.click(n.x,n.y);await p.waitForTimeout(100);
 assert.equal(await p.evaluate(()=>TREE.run),target.run);
 // Keyboard navigation is the other supported selection route.
 await p.keyboard.press('ArrowLeft');await p.waitForTimeout(100);
 assert.equal(await p.evaluate(()=>TREE.run),await p.evaluate(()=>RECORD.pass));
 await p.keyboard.press('Escape');await p.waitForTimeout(500);
 assert.equal(await p.evaluate(()=>TREE.run),feed.run.id);
 // A genuinely empty run stays empty even when the repo union is populated.
 const empty=feed.cloth.rows.find(r=>r.ended&&!r.trail.length&&!feed.cloth.rows.some(c=>c.parent===r.run));
 const ec=await p.evaluate(id=>{const i=RECORD.passes.findIndex(r=>r.run===id),g=RECORD.geom;return {x:g.x0+(i+.5)*g.pw,y:g.base-1};},empty.run);
 await p.mouse.click(ec.x,ec.y);await p.mouse.move(10,200);await p.waitForTimeout(2000);
 assert.equal(await p.evaluate(()=>TREE.run),empty.run);
 assert.equal(await p.evaluate(()=>nodes.length),0);
 await p.screenshot({path:out+'/03-empty.png'});
 receipt.empty=empty.run;
 // Refresh an unchanged run with a new place absent from the global tree.
 await p.keyboard.press('Escape');
 receipt.refresh=await p.evaluate(()=>{const d=structuredClone(S);d.cloth.trail_limit=8;const r=d.cloth.rows.find(r=>r.run===d.run.id);r.trail=[{path:'fresh/place.py',at:d.at}];ingest(d);return {paths:nodes.map(n=>n.path),limit:TREE.limit};});
 assert.ok(receipt.refresh.paths.includes('fresh/place.py'));
 const handPaths=new Set(feed.cloth.rows.filter(r=>r.parent===feed.run.id).flatMap(r=>r.trail.map(t=>t.path)));
 for(const t of feed.cloth.rows.find(r=>r.run===feed.run.id).trail)
   if(!handPaths.has(t.path))assert.ok(!receipt.refresh.paths.includes(t.path));
 assert.equal(receipt.refresh.limit,8);
 // Attribute by the direct parent relation, coalesce shared paths, and never
 // borrow the parent run's chunks when a strand alone touched that path.
 receipt.attribution=await p.evaluate(()=>{
   const d=structuredClone(S);d.run={id:'seat',name:'Seat'};
   d.cloth={trail_limit:8,rows:[
     {run:'seat',trail:[{path:'shared.py',at:d.at}]},
     {run:'hand',parent:'seat',trail:[{path:'shared.py',at:d.at},{path:'delegated.py',at:d.at}]},
     {run:'grandchild',parent:'hand',trail:[{path:'grandchild.py',at:d.at}]},
     {run:'other',trail:[{path:'other.py',at:d.at}]}]};
   d.beads=[{n:999,chunks:[{rel:'delegated.py',kind:'read',from:1,to:8}]}];
   ingest(d);
   const shared=byPath.get('shared.py');
   const result={paths:nodes.map(n=>n.path).sort(),own:shared.own,hands:shared.hands,chunks:[...CHUNKS.keys()]};
   result.liveSelectionEnded=treeScope(d,'hand').ended;
   const target={node:shared};ingest(structuredClone(d));
   result.sameTarget=sameTarget(target,{node:byPath.get('shared.py')});
   return result;
 });
 assert.deepEqual(receipt.attribution.paths,['delegated.py','shared.py']);
 assert.equal(receipt.attribution.own,true);
 assert.equal(receipt.attribution.liveSelectionEnded,null,'another live run must not be labelled past');
 assert.deepEqual(receipt.attribution.hands,['hand']);
 assert.equal(receipt.attribution.chunks.length,0);
 assert.equal(receipt.attribution.sameTarget,true,'an identical refresh must not invent travel');
 await p.waitForTimeout(100);
 receipt.returnToLive=await p.evaluate(()=>{
   const d=structuredClone(S);d.shuttle={state:'working'};
   d.beads=[{n:1000,place_kind:'file',places:['shared.py'],chunks:[]}];
   ingest(d);
   const pod=pods.find(p=>p.kind==='resident');
   RECORD.pass='hand';refreshTree();RECORD.pass=null;refreshTree();
   return {target:pod.target?.node?.path,pings:pings.length};
 });
 assert.equal(receipt.returnToLive.target,'shared.py');
 assert.equal(receipt.returnToLive.pings,0,'returning to live must not replay old acts');
 receipt.errors=errors;assert.deepEqual(errors,[]);
 await writeFile(out+'/receipt.json',JSON.stringify(receipt,null,2));
 console.log(JSON.stringify({out,gpu:receipt.gpu,default:receipt.default.paths.length,past:receipt.past.paths.length,fx:receipt.default.fx,errors},null,2));
}finally{await browser.close();server.close();}
