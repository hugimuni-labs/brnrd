// Render scene.html to an H.264 MP4, one screenshot per frame.
//
//   node media/video/first-hire/render.mjs                  # full video
//   node media/video/first-hire/render.mjs --stills 3,9.5   # PNG stills for review
//
// Needs ffmpeg on PATH and Playwright's Chromium (reused from the frontend's
// node_modules, so nothing new is installed).
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import os from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(resolve(here, '../../../src/frontend/package.json'));
const { chromium } = require('playwright');

const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i < 0 ? dflt : process.argv[i + 1];
};
const FPS = 30;
const out = resolve(arg('out', join(here, 'brnrd-first-hire.mp4')));
const work = resolve(arg('work', join(os.tmpdir(), 'brnrd-first-hire-frames')));
const stills = arg('stills', null);
const workers = Number(arg('workers', 6));

const browser = await chromium.launch();
const url = pathToFileURL(join(here, 'scene.html')).href;
async function page() {
  const p = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  await p.goto(url);
  await p.waitForFunction(() => window.__ready === true);
  return p;
}
const shot = async (p, t, path) => {
  await p.evaluate((t) => window.render(t), t);
  await p.screenshot({ path, type: 'png' });
};

mkdirSync(work, { recursive: true });
if (stills) {
  const p = await page();
  for (const t of stills.split(',').map(Number)) {
    const path = join(work, `still-${t.toFixed(2)}.png`);
    await shot(p, t, path);
    console.log(path);
  }
} else {
  const duration = await (await page()).evaluate(() => window.DURATION);
  const total = Math.round(duration * FPS);
  rmSync(work, { recursive: true, force: true });
  mkdirSync(work, { recursive: true });
  let next = 0, done = 0;
  await Promise.all(Array.from({ length: workers }, async () => {
    const p = await page();
    while (next < total) {
      const f = next++;
      await shot(p, f / FPS, join(work, `f_${String(f).padStart(5, '0')}.png`));
      if (++done % 150 === 0) console.log(`${done}/${total}`);
    }
  }));
  const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS),
    '-i', join(work, 'f_%05d.png'), '-c:v', 'libx264', '-preset', 'slow', '-crf', '17',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], { stdio: 'inherit' });
  if (r.status !== 0) process.exit(r.status ?? 1);
  console.log(out);
}
await browser.close();
