// Renders the film deterministically, frame by frame, in headless Chromium.
//   node render.js                      → brnrd-brand-film.mp4 (1080², 60 fps)
//   node render.js --stills 1.2,5.5     → stills/t-1.20.png …
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const FPS = 60;
const args = process.argv.slice(2);
const stillsArg = args.includes('--stills') ? args[args.indexOf('--stills') + 1] : null;
const outFile = args.includes('--out') ? args[args.indexOf('--out') + 1] : path.join(__dirname, 'brnrd-brand-film.mp4');

(async () => {
  const browser = await chromium.launch({ args: ['--allow-file-access-from-files'] });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1080 } });
  page.on('pageerror', e => { console.error('page error:', e); process.exit(1); });
  await page.goto('file://' + path.join(__dirname, 'index.html'));
  await page.waitForFunction(() => window.READY === true);
  const dur = await page.evaluate(() => window.DUR);

  if (stillsArg) {
    const dir = path.join(__dirname, 'stills'); fs.mkdirSync(dir, { recursive: true });
    for (const t of stillsArg.split(',').map(Number)) {
      const b64 = await page.evaluate(t => window.frameAt(t), t);
      fs.writeFileSync(path.join(dir, `t-${t.toFixed(2)}.png`), Buffer.from(b64, 'base64'));
    }
    await browser.close(); return;
  }

  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '15', '-tune', 'film', '-pix_fmt', 'yuv420p',
    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-movflags', '+faststart', outFile],
    { stdio: ['pipe', 'inherit', 'inherit'] });
  const n = Math.round(dur * FPS);
  const t0 = Date.now();
  for (let f = 0; f < n; f++) {
    const b64 = await page.evaluate(t => window.frameAt(t), f / FPS);
    if (!ff.stdin.write(Buffer.from(b64, 'base64'))) await new Promise(r => ff.stdin.once('drain', r));
    if (f % 60 === 0) console.log(`frame ${f}/${n}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on('close', r));
  await browser.close();
  console.log('wrote', outFile);
})();
