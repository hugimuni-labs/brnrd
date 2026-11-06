// Render every phone shot once bundled: one 1080p mp4 per shot, and half-scale stills at a few frames each.
// node src/gen/chat/render.mjs [out dir, default ~/Desktop/brnrd/booth/gen/chat] [shot keys...]
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {mkdirSync} from 'node:fs';
import {homedir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const script = (await import(path.join(here, 'script.json'), {with: {type: 'json'}})).default;
const out = process.argv[2] || path.join(homedir(), 'Desktop/brnrd/booth/gen/chat');
const only = process.argv.slice(3);
mkdirSync(out, {recursive: true});

const serveUrl = await bundle({entryPoint: path.join(here, 'index.jsx')});
for (const s of script.shots.filter((s) => !only.length || only.includes(s.key))) {
  const composition = await selectComposition({serveUrl, id: s.key, inputProps: {shot: s.key}});
  const n = composition.durationInFrames;
  for (const f of [...new Set([0.25, 0.5, 0.75, 1].map((u) => Math.min(n - 1, Math.round(u * n))))]) {
    await renderStill({serveUrl, composition, frame: f, scale: 0.5, output: path.join(out, `${s.key}-f${String(f).padStart(3, '0')}.png`), inputProps: {shot: s.key}});
  }
  await renderMedia({serveUrl, composition, codec: 'h264', crf: 16, outputLocation: path.join(out, `${s.key}.mp4`), inputProps: {shot: s.key}});
  console.log(s.key, n, 'frames');
}
