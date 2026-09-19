// Visual rehearsal for field7 — the derelict. Same camera at every altitude,
// every state driven by a REAL key or click, so a shot is a receipt for the
// interaction and not only for the renderer.
//
//   PYTHONPATH=src python -m brr loom --port 7792      (serve THIS checkout — see README)
//   PLAYWRIGHT_MODULE=<playwright/index.mjs> LOOM_URL=http://127.0.0.1:7792 OUT=/tmp/f7 node field7-shots.mjs
//
// NEVER quote a frame time from plain headless chromium on this machine: it
// falls back to SwiftShader. headless:false + --use-angle=metal is what measured.
import { mkdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE
  || '/Users/gurio/Source/Projects/brnrd/src/frontend/node_modules/playwright/index.mjs'));
const base=(process.env.LOOM_URL||'http://127.0.0.1:7792')+'/loom/dev/field7.html?perf=1', out=process.env.OUT||'/tmp/f7';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:false,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const ctx=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
const p=await ctx.newPage(); const errs=[]; p.on('pageerror',e=>errs.push(e.message));
const shot=n=>p.screenshot({path:`${out}/${n}.png`});
const type=async s=>{await p.keyboard.type(s);await p.keyboard.press('Enter');await p.waitForTimeout(1100);};
await p.goto(base,{waitUntil:'load'}); await p.waitForTimeout(3500);
await shot('1-derelict');                                        // altitude 3, resting
await p.mouse.move(700,180); await p.waitForTimeout(500); await shot('2-derelict-hover');
await type('crew'); await shot('3-derelict-crew');               // who is out, who came back
await type('trail seat'); await shot('4-derelict-trail');        // the seat's trail lit
await p.keyboard.press('Escape'); await p.keyboard.press('2'); await p.waitForTimeout(1500); await shot('5-deck');   // altitude 2
await p.mouse.move(60,125); await p.waitForTimeout(500); await shot('6-deck-hover-file');
await type('go src/brr'); await shot('7-room');                  // altitude 1, the chunk ground per room
await type('look daemon.py'); await type('items the-loom'); await type('scan'); await shot('8-room-console');
await p.keyboard.press('Escape'); await p.waitForTimeout(1200); await shot('9-deck-back');
await p.goto(base+'&fx=off',{waitUntil:'load'}); await p.waitForTimeout(3000); await shot('10-derelict-fx-off');   // the control
const f=await p.evaluate(()=>window.__loomFrame); console.log('frame',JSON.stringify(f)); console.log('errors',JSON.stringify(errs));
await browser.close();
