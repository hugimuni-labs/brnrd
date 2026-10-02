// frame stills from one bundle: node scripts/stills.mjs OUTDIR f1 f2 ... (frames, or a:b:step ranges)  REBUNDLE=1 after edits; SCALE=0.5
import {bundle} from '@remotion/bundler';
import {openBrowser, renderStill, selectComposition} from '@remotion/renderer';
import {existsSync, mkdirSync} from 'node:fs';
import path from 'node:path';
const [out, ...args] = process.argv.slice(2);
mkdirSync(out, {recursive: true});
const frames = args.flatMap((a) => { if (!a.includes(':')) return [Number(a)]; const [x, y, s] = a.split(':').map(Number); const r = []; for (let f = x; f <= y; f += s || 1) r.push(f); return r; });
const serveUrl = process.env.REBUNDLE || !existsSync('build/index.html')
  ? await bundle({entryPoint: path.resolve('src/index.ts'), outDir: path.resolve('build'), publicDir: path.resolve('public')})
  : path.resolve('build');
const browser = await openBrowser('chrome');
const composition = await selectComposition({serveUrl, id: 'Film', puppeteerInstance: browser, inputProps: {muted: true}});
for (const frame of frames) {
  await renderStill({composition, serveUrl, frame, inputProps: {muted: true}, output: path.join(out, `f_${String(frame).padStart(5, '0')}.jpg`), imageFormat: 'jpeg', jpegQuality: 80, puppeteerInstance: browser, scale: Number(process.env.SCALE || 0.25), timeoutInMilliseconds: 120000});
}
await browser.close({silent: true});
console.log('stills', frames.length);
