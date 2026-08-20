// Click-through of the home page at phone width with a live run + strand,
// screenshot per step. Output: /tmp/walk/NN-<step>.png
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';
const OUT = '/tmp/walk-garage'; const PORT = 5188;
const ROUTES = fixtures.buildRoutes(fixtures.DEFAULT_SCALE);
const now = new Date().toISOString();
const run = (id, extra) => ({ id, kind: 'run', stream: 'brr', label: 'hugimuni-labs/brnrd', name: id, run_id: id,
  repo_label: 'hugimuni-labs/brnrd', started_at: now, last_seen: now, parent_run_id: null, is_subspawn: false,
  runner: { shell: 'claude', core: 'opus', profile: 'claude-opus' }, phase: 'running', card_text: 'reading the diff whole',
  card_updated_at: now, relics_counts: { commit: 1 }, ...extra });
ROUTES['/v1/dashboard/live-runs'] = { ...fixtures.liveRuns, runs: [
  run('run-260820-1926-7ar9', { name: 'the-garage-and-the-bench' }),
  run('run-260820-1930-s1', { name: 'strand: fix #1369', parent_run_id: 'run-260820-1926-7ar9', is_subspawn: true, runner: { shell: 'codex', core: 'gpt-5.6-sol', profile: 'codex-full' } })
]};
async function waitFor(url){for(let i=0;i<60;i++){try{const r=await fetch(url);if(r.ok||r.status===404)return}catch{}await delay(500)}throw new Error('no server')}
await mkdir(OUT,{recursive:true});
const vite = spawn('npx',['vite','dev','--port',String(PORT),'--strictPort'],{stdio:'ignore'});
try{
  await waitFor(`http://localhost:${PORT}/`);
  const b = await chromium.launch(); const ctx = await b.newContext({viewport:{width:390,height:844},reducedMotion:'no-preference',hasTouch:true,isMobile:true});
  const page = await ctx.newPage();
  await page.route('**/v1/dashboard/**', r=>{const p=new URL(r.request().url()).pathname; const body=ROUTES[p]; return r.fulfill({status:body?200:404,contentType:'application/json',body:JSON.stringify(body??{})})});
  for (const r of ['a','b','c']) {
    await page.goto(`http://localhost:${PORT}/garage/${r}`,{waitUntil:'networkidle'}); await delay(900);
    const shot=async(name)=>{await page.screenshot({path:`${OUT}/${r}-${name}.png`}); console.log(r,name,'scrollY',await page.evaluate(()=>scrollY))};
    await shot('1-top');
    const btns=await page.locator('button').allInnerTexts(); console.log(r,'buttons:',btns.map(t=>t.replace(/\s+/g,' ').slice(0,40)).join(' | '));
    const shellBtn=page.locator('button').filter({hasText:/codex/i}).first(); if(await shellBtn.count()){await shellBtn.click(); await delay(700); await shot('2-shell-tapped');}
    const toggle=page.locator('button').filter({hasText:/▾|drawer|bench|project/i}).first(); if(await toggle.count()){await toggle.click(); await delay(700); await shot('3-toggle');}
    await page.evaluate(()=>scrollTo(0,900)); await delay(700); await shot('4-scrolled-900');
    console.log(r,'doc height',await page.evaluate(()=>document.documentElement.scrollHeight),'hscroll',await page.evaluate(()=>document.documentElement.scrollWidth));
  }
  await b.close();
} finally { vite.kill('SIGTERM'); }
