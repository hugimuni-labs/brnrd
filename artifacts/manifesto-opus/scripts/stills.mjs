// stills at given seconds from one bundle: node scripts/stills.mjs OUTDIR t1 t2 ...  (REBUNDLE=1 after edits)
import {bundle} from '@remotion/bundler';
import {openBrowser, renderStill, selectComposition} from '@remotion/renderer';
import {existsSync, mkdirSync} from 'node:fs';
import path from 'node:path';
const [out, ...ts] = process.argv.slice(2);
mkdirSync(out, {recursive: true});
const serveUrl = process.env.REBUNDLE || !existsSync('build/index.html')
  ? await bundle({entryPoint: path.resolve('src/index.ts'), outDir: path.resolve('build'), publicDir: path.resolve('public')})
  : path.resolve('build');
const browser = await openBrowser('chrome');
const composition = await selectComposition({serveUrl, id: 'Film', puppeteerInstance: browser, inputProps: {muted: true}});
for (const t of ts) {
  const frame = Math.round(Number(t) * 30);
  await renderStill({composition, serveUrl, frame, inputProps: {muted: true}, output: path.join(out, `s_${String(frame).padStart(5, "0")}.jpg`), imageFormat: 'jpeg', jpegQuality: 80, puppeteerInstance: browser, scale: 0.5, timeoutInMilliseconds: 120000});
  process.stdout.write('.');
}
await browser.close({silent: true});
console.log(' done');
