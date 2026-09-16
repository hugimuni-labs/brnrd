// Visual rehearsal, not a canvas unit suite. Inspect every saved image.
import { mkdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE || '/tmp/shotwork/node_modules/playwright/index.mjs'));
const base = process.env.LOOM_URL || 'http://127.0.0.1:7778';
const output = process.env.CAPTURE_DIR || '/tmp/dungeon-gen1';
await mkdir(output,{recursive:true});
const browser = await chromium.launch({headless:true});
const errors = [], receipts = [];
for (const fixture of ['live','empty','three','eighty']) for (const [width,height] of [[1440,900],[390,844]]) {
  const context=await browser.newContext({viewport:{width,height},reducedMotion:'no-preference'});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`${base}/loom/?fixture=${fixture}`);
  await page.waitForFunction(()=>document.querySelector('canvas').dataset.ready==='true');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.screenshot({path:resolve(output,`${fixture}-${width}x${height}.png`)});
  const before=await page.locator('canvas').screenshot();await page.waitForTimeout(750);const after=await page.locator('canvas').screenshot();
  assert.ok(before.equals(after),'A frozen fixture has no idle animation');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
  await page.getByRole('button',{name:'All measured readings',exact:true}).click();
  assert.equal(await page.locator('#bench').isVisible(),true);
  await page.keyboard.press('Escape');
  if(fixture==='eighty'){
    // The first decision has a measured downstream room.
    assert.match(await page.locator('#selection').innerText(),/opens w-2/);
    await page.getByRole('button',{name:'Map',exact:true}).click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.screenshot({path:resolve(output,`overview-${width}x${height}.png`)});
    await page.getByRole('button',{name:'w-2 Prepare the rollback kit',exact:true}).focus();
    await page.keyboard.press('Enter');
    assert.match(await page.locator('#selection').innerText(),/revealed by your hand/);
    await page.getByRole('button',{name:'Recenter',exact:true}).click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.screenshot({path:resolve(output,`hub-${width}x${height}.png`)});
    await page.getByRole('button',{name:'archive archive',exact:true}).focus();await page.keyboard.press('Enter');
    assert.equal(await page.locator('#bench').isVisible(),true);
    await page.keyboard.press('j');await page.keyboard.press('l');
    assert.equal(await page.locator('.tree-row.active').count(),1);
    assert.equal(await page.getByText('node_modules',{exact:false}).count(),0);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.screenshot({path:resolve(output,`archive-${width}x${height}.png`)});
  }
  // The generative rule's own test: the first view is framed on ring 1 at every population.
  const frame=await page.evaluate(()=>({z:window.__loomFrame?.z,ring1:window.__loomFrame?.ring1,rooms:Number(document.querySelector('canvas').dataset.rooms)}));
  receipts.push({fixture,width,height,still:true,overflow:false,...frame,screen_ring1:frame.z&&frame.ring1?Math.round(frame.z*frame.ring1*2):null});
  await context.close();
}
// Drive a page against the real endpoint shape, including hostile-looking text.
{
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/loom/page/item?*',r=>r.fulfill({json:{id:'w-1',type:'decision',title:'The token mint scope',state:'ready',body:'<script>throw Error("markup executed")</script>\nA plain-text page.'}}));
 await page.goto(`${base}/loom/?fixture=live`);await page.waitForFunction(()=>document.querySelector('canvas').dataset.ready==='true');
 await page.locator('canvas').focus();await page.keyboard.press('Enter');
 await page.getByText('A plain-text page.',{exact:false}).waitFor();
 assert.equal(await page.locator('#page script').count(),0);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.screenshot({path:resolve(output,'bench-page.png')});
 await page.close();
}
await browser.close();
// The generative rule's own test: three rooms and eighty open in the same frame, ring 1 on the same glass.
for (const width of [1440,390]) {
  const sizes=[...new Set(receipts.filter(r=>r.width===width&&r.fixture!=='empty').map(r=>r.screen_ring1))];
  assert.equal(sizes.length,1,`ring 1 opens at one size per viewport; got ${sizes.join(', ')} at ${width}`);
}
assert.deepEqual(errors,[]);
console.log(JSON.stringify({receipts,errors,output},null,2));
