// manifesto v2 — the cut. Frame-exact edit list over the opus worlds + a new editorial layer.
// Grammar (from the MIDA study): one subject (#482) re-rendered across media; readable holds with tiny internal change;
// bursts of 1–6 frame states; hard swaps of a dominant field; type as architecture; stepped, not tweened, motion.
import {
  W, H, V3, R, Cam, cam, view, PERM, camLerp, clamp, lerp, prog, ss, eio, eout, ein, expo, hash, rng, off, ctx2,
  bloom, bitmapify, tear, fracture, grain, vignette, Fault,
} from './engine';
import { OVR, S, build, blend, Spec, C, PAL, codexWorld, claudeWorld, mistralWorld, carry } from './scenes';
import { F } from './fonts';
import { IMG } from './assets';

export const FPS = 30;
// v2 pass 2: the edit list below is written in *source* frames; DROPS removes spans (jump cuts), so the output is
// tighter than the source without renumbering every shot. audio/score.py applies the same map.
export const DROPS: [number, number][] = [[72, 90], [104, 126], [895, 912]];
export const SRC_F = 1260;
export const DURATION_F = SRC_F - DROPS.reduce((a, [x, y]) => a + y - x + 1, 0);
export function srcFrame(fo: number) { let f = fo; for (const [x, y] of DROPS) if (f >= x) f += y - x + 1; return f; }

// ======================================================================= 2D helpers
let LIGHT = false; // finish pass: paper vs dark
function bg(ctx: CanvasRenderingContext2D, col: string) {
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over'; ctx.filter = 'none';
  ctx.fillStyle = col; ctx.fillRect(0, 0, W, H); ctx.restore();
}
function txt(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, font: string, col: string, align: CanvasTextAlign = 'center', base: CanvasTextBaseline = 'middle', track = 0, a = 1) {
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha = a; ctx.font = font; ctx.fillStyle = col; ctx.textAlign = align; ctx.textBaseline = base; (ctx as any).letterSpacing = track + 'px'; ctx.fillText(s, x, y); ctx.restore();
}
function measure(ctx: CanvasRenderingContext2D, s: string, font: string, track = 0) {
  ctx.save(); ctx.font = font; (ctx as any).letterSpacing = track + 'px'; const m = ctx.measureText(s).width; ctx.restore(); return m;
}
// font size that makes `s` exactly `w` px wide
const fit = (ctx: CanvasRenderingContext2D, s: string, fam: (n: number) => string, w: number, track = 0) => (100 * w) / measure(ctx, s, fam(100), track);
// the seam: white hairline core + spectral hairlines a couple of px off (dark ground). On paper: ink core + subtractive fringe.
function hair(ctx: CanvasRenderingContext2D, x0: number, y0: number, x1: number, y1: number, a = 1, prism = 1, w = 1, paper = false) {
  if (a <= 0) return;
  ctx.save(); ctx.globalCompositeOperation = paper ? 'multiply' : 'lighter';
  const dx = x1 - x0, dy = y1 - y0, L = Math.hypot(dx, dy) || 1, nx = -dy / L, ny = dx / L;
  const ln = (o: number, col: string, lw: number) => { ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.beginPath(); ctx.moveTo(x0 + nx * o, y0 + ny * o); ctx.lineTo(x1 + nx * o, y1 + ny * o); ctx.stroke(); };
  if (paper) { ln(0, `rgba(20,18,16,${a})`, w); if (prism > 0) { ln(2.2, `rgba(0,170,220,${0.7 * a * prism})`, 0.9); ln(-2.2, `rgba(230,40,120,${0.7 * a * prism})`, 0.9); } }
  else { ln(0, `rgba(255,255,255,${a})`, w); if (prism > 0) { ln(2.2, `rgba(255,50,110,${0.55 * a * prism})`, 0.8); ln(-2.2, `rgba(60,255,220,${0.55 * a * prism})`, 0.8); ln(4.6, `rgba(130,100,255,${0.3 * a * prism})`, 0.6); } }
  ctx.restore();
}
function blit(ctx: CanvasRenderingContext2D, c: HTMLCanvasElement, a = 1, comp: GlobalCompositeOperation = 'source-over', dx = 0, dy = 0) {
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = a; ctx.globalCompositeOperation = comp; ctx.drawImage(c, dx, dy); ctx.restore();
}
function lift(ctx: CanvasRenderingContext2D, a: number) { ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = a; ctx.globalCompositeOperation = 'lighter'; ctx.drawImage(ctx.canvas, 0, 0); ctx.restore(); }
function invert(ctx: CanvasRenderingContext2D) { ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalCompositeOperation = 'difference'; ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H); ctx.restore(); }
// render something into a named full-frame offscreen
function layer(name: string, draw: (c: CanvasRenderingContext2D) => void) {
  const c = off(name), x = ctx2(c); x.setTransform(1, 0, 0, 1, 0, 0); x.globalAlpha = 1; x.globalCompositeOperation = 'source-over'; x.filter = 'none'; x.clearRect(0, 0, W, H); draw(x); return c;
}
// frozen frames, rendered once per tab
const snaps = new Map<string, HTMLCanvasElement>();
function snap(name: string, draw: (c: CanvasRenderingContext2D) => void) {
  let c = snaps.get(name);
  if (!c) { c = document.createElement('canvas'); c.width = W; c.height = H; const x = ctx2(c); draw(x); x.setTransform(1, 0, 0, 1, 0, 0); snaps.set(name, c); }
  return c;
}
function sample(src: HTMLCanvasElement, w: number, h: number) {
  const s = off('smp' + w + 'x' + h, w, h), sc = s.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  sc.setTransform(1, 0, 0, 1, 0, 0); sc.globalCompositeOperation = 'copy'; sc.imageSmoothingEnabled = true; sc.drawImage(src, 0, 0, w, h);
  return sc.getImageData(0, 0, w, h);
}
// print media -------------------------------------------------------------
function halftone(ctx: CanvasRenderingContext2D, src: HTMLCanvasElement, cell: number, ink: string, paper: string, fromBright: boolean, gain = 1.1) {
  const w = Math.ceil(W / cell), h = Math.ceil(H / cell), d = sample(src, w, h).data;
  bg(ctx, paper); ctx.save(); ctx.fillStyle = ink; ctx.beginPath();
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4, l = (0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]) / 255;
    const v = clamp((fromBright ? l : 1 - l) * gain), r = cell * 0.7 * Math.sqrt(v);
    if (r > 0.5) { const cx = x * cell + cell / 2 + (y % 2) * cell * 0.5, cy = y * cell + cell / 2; ctx.moveTo(cx + r, cy); ctx.arc(cx, cy, r, 0, Math.PI * 2); }
  }
  ctx.fill(); ctx.restore();
}
// photocopy: threshold + toner speckle + soft edge, from a source; each generation loses detail
function xerox(ctx: CanvasRenderingContext2D, src: HTMLCanvasElement, lvl: number, ink: number[], paper: number[], seed: number, speck = 0.015, scale = 2, fromBright = false) {
  const w = W / scale, h = H / scale, img = sample(src, w, h), d = img.data, r = rng(seed);
  for (let i = 0; i < d.length; i += 4) {
    const l = (0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]) / 255 + (r() - 0.5) * 0.18;
    let on = fromBright ? l > lvl : l < lvl; if (r() < speck) on = !on;
    const q = on ? ink : paper; d[i] = q[0]; d[i + 1] = q[1]; d[i + 2] = q[2]; d[i + 3] = 255;
  }
  const s = off('xr', w, h); (s.getContext('2d') as CanvasRenderingContext2D).putImageData(img, 0, 0);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.imageSmoothingEnabled = true; ctx.globalCompositeOperation = 'copy'; ctx.drawImage(s, 0, 0, W, H); ctx.restore();
}
// raw raster: the deliberately ugly frame
function crush(ctx: CanvasRenderingContext2D, k: number) {
  const s = off('crush', Math.round(W / k), Math.round(H / k)), sc = ctx2(s);
  sc.globalCompositeOperation = 'copy'; sc.imageSmoothingEnabled = true; sc.drawImage(ctx.canvas, 0, 0, s.width, s.height);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.imageSmoothingEnabled = false; ctx.globalCompositeOperation = 'copy'; ctx.drawImage(s, 0, 0, W, H); ctx.restore();
}
// misregistered CMY-ish print of a draw call on paper
function printMisreg(ctx: CanvasRenderingContext2D, paper: string, draw: (c: CanvasRenderingContext2D, col: string) => void, o = 6) {
  bg(ctx, paper);
  const plate = (col: string, dx: number, dy: number) => { const c = layer('plate', (x) => { x.translate(dx, dy); draw(x, col); }); blit(ctx, c, 1, 'multiply'); };
  plate('rgb(0,175,225)', -o, o * 0.5); plate('rgb(235,40,125)', o * 0.8, -o * 0.3); plate('rgb(24,22,20)', 0, 0);
}
// logo silhouette (shell logos only)
function logo(ctx: CanvasRenderingContext2D, name: string, cx: number, cy: number, size: number, tint: string | null, pixel = false) {
  const im = IMG[name]; if (!im) return;
  const c = off('logo', 512, 512), x = ctx2(c); x.setTransform(1, 0, 0, 1, 0, 0); x.globalCompositeOperation = 'copy'; x.imageSmoothingEnabled = !pixel; x.drawImage(im, 0, 0, 512, 512);
  if (tint) { x.globalCompositeOperation = 'source-in'; x.fillStyle = tint; x.fillRect(0, 0, 512, 512); }
  ctx.save(); ctx.imageSmoothingEnabled = !pixel; ctx.drawImage(c, cx - size / 2, cy - size / 2, size, size); ctx.restore();
}
// annotations that live for a few frames
function bracket(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, col: string, a = 1, L = 18) {
  ctx.save(); ctx.globalAlpha = a; ctx.strokeStyle = col; ctx.lineWidth = 1.2; ctx.beginPath();
  for (const [px, py, sx, sy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) { ctx.moveTo(px + sx * L, py); ctx.lineTo(px, py); ctx.lineTo(px, py + sy * L); }
  ctx.stroke(); ctx.restore();
}
function tag(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, col: string, a = 1, size = 15, align: CanvasTextAlign = 'left') {
  txt(ctx, s, x, y, `500 ${size}px ${F.MONO}`, col, align, 'alphabetic', 2, a);
}
const withCam = (sp: Spec | Cam, fn: () => void) => { OVR.cam = (sp as Cam).m ? (sp as Cam) : build(sp as Spec); try { fn(); } finally { OVR.cam = null; } };
const mapT = (f: number, f0: number, f1: number, t0: number, t1: number) => lerp(t0, t1, clamp((f - f0) / Math.max(1, f1 - f0)));

// ======================================================================= the artifact: #482 in every medium
const PAPER = '#ECE8DF', IVORY = '#EDE5D4', INK = '#161412';
type Medium = 'print' | 'pixel' | 'crop' | 'halftone' | 'term' | 'doto' | 'serif' | 'barcode' | 'outline' | 'thermal';
function art(ctx: CanvasRenderingContext2D, m: Medium, f: number) {
  const A = (n: number) => `400 ${n}px ${F.ANTON}`;
  LIGHT = ['print', 'halftone', 'serif', 'barcode'].includes(m);
  if (m === 'print') {
    printMisreg(ctx, PAPER, (x, col) => { txt(x, '#482', W / 2, H / 2 + 20, A(fit(x, '#482', A, 1250)), col); }, 9);
    tag(ctx, 'feat/offline-sync → main', 120, 1000, INK, 0.8); tag(ctx, 'PR', 1800, 1000, INK, 0.8, 15, 'right');
  } else if (m === 'pixel') {
    bg(ctx, '#000'); txt(ctx, '#482', W / 2, H / 2, `400 ${fit(ctx, '#482', (n) => `400 ${n}px ${F.PIX}`, 1500)}px ${F.PIX}`, '#fff');
  } else if (m === 'crop') {
    bg(ctx, '#000'); txt(ctx, '48', W * 0.62, H * 0.56, A(2300), '#fff');
  } else if (m === 'halftone') {
    const c = layer('ht-src', (x) => { x.fillStyle = '#fff'; x.fillRect(0, 0, W, H); txt(x, '#482', W / 2, H / 2, A(860), '#000'); });
    halftone(ctx, c, 16, INK, PAPER, false, 1.3);
  } else if (m === 'term') {
    bg(ctx, '#000'); const y = H / 2;
    txt(ctx, '+ retry(on: 409, max: 3)', 300, y, `500 34px ${F.MONO}`, '#fff', 'left');
    txt(ctx, 'feat/offline-sync  #482', 300, y + 54, `400 22px ${F.MONO}`, 'rgba(255,255,255,0.5)', 'left', 'middle', 1);
    if (f % 2 === 0) { ctx.fillStyle = '#fff'; ctx.fillRect(300 + measure(ctx, '+ retry(on: 409, max: 3)', `500 34px ${F.MONO}`) + 8, y - 20, 18, 40); }
  } else if (m === 'doto') {
    bg(ctx, '#000'); txt(ctx, '#482', W / 2, H / 2, `900 ${fit(ctx, '#482', (n) => `900 ${n}px ${F.DOTO}`, 1300)}px ${F.DOTO}`, '#fff');
  } else if (m === 'serif') {
    bg(ctx, IVORY); txt(ctx, '#482', W / 2, H / 2 + 40, `italic 400 ${fit(ctx, '#482', (n) => `italic 400 ${n}px ${F.SERIF}`, 1100)}px ${F.SERIF}`, INK);
  } else if (m === 'barcode') {
    bg(ctx, PAPER); txt(ctx, '#482 retry on 409', W / 2, H / 2 - 40, `400 ${fit(ctx, '#482 retry on 409', (n) => `400 ${n}px ${F.BAR}`, 1500)}px ${F.BAR}`, INK);
    tag(ctx, '#482 · feat/offline-sync · 5 commits · tests ✓', W / 2, H / 2 + 220, INK, 0.85, 22, 'center');
  } else if (m === 'outline') {
    bg(ctx, '#000'); ctx.save(); ctx.font = A(fit(ctx, '#482', A, 1300)); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (let k = 9; k >= 0; k--) { ctx.strokeStyle = `rgba(255,255,255,${k ? 0.12 : 1})`; ctx.lineWidth = 1.4; ctx.strokeText('#482', W / 2 + k * 9, H / 2 - k * 6); }
    ctx.restore();
  } else if (m === 'thermal') {
    const c = layer('th-src', (x) => { x.fillStyle = '#000'; x.fillRect(0, 0, W, H); x.filter = 'blur(22px)'; txt(x, '#482', W / 2, H / 2, A(900), '#fff'); x.filter = 'none'; txt(x, '#482', W / 2, H / 2, A(900), 'rgba(255,255,255,0.9)'); });
    xerox(ctx, c, 0.32, [235, 235, 230], [24, 24, 26], f, 0.004, 3, true);
  }
}
// brnrd: only ever type
const MARKS: [string, (n: number) => string, string, string][] = [
  ['brnrd', (n) => `800 ${n}px ${F.SANS}`, '#000', '#fff'],
  ['brωrd', (n) => `700 ${n}px ${F.BODONI}`, PAPER, INK],
  ['b^n^d', (n) => `400 ${n}px ${F.PIX}`, '#000', '#fff'],
  ['b>_<d', (n) => `600 ${n}px ${F.MONO}`, '#fff', '#000'],
  ['b·_·d', (n) => `900 ${n}px ${F.DOTO}`, '#000', '#fff'],
  ['BRNRD', (n) => `400 ${n}px ${F.ANTON}`, '#000', '#fff'],
  ['brnrd', (n) => `italic 400 ${n}px ${F.SERIF}`, IVORY, INK],
  ['b^_^d', (n) => `500 ${n}px ${F.MONO}`, '#000', '#fff'],
];
function mark(ctx: CanvasRenderingContext2D, i: number, width: number, vertical = false) {
  const [s, fam, bgc, fg] = MARKS[i % MARKS.length]; bg(ctx, bgc); LIGHT = bgc !== '#000';
  if (vertical) { ctx.save(); ctx.translate(W / 2, H / 2); ctx.rotate(-Math.PI / 2); txt(ctx, s, 0, 10, fam(fit(ctx, s, fam, H * 0.98)), fg); ctx.restore(); }
  else txt(ctx, s, W / 2, H / 2, fam(fit(ctx, s, fam, width)), fg);
}

// ======================================================================= opus worlds, re-framed
const CAM = {
  codexFlat: S(0, 90, [-130, 0, -150], 2.3),
  codexAxon: S(-36, 27, [-90, 40, -170], 1.85),
  codexClose: S(-50, 18, [40, 20, -180], 2.3, 0.4),
  wall: S(-62, 10, [180, 420, -230], 1.3, 0.35),
  claudeFlat: S(0, 90, [-420, 0, 600], 1.25),
  mistral: S(-28, 34, [40, 30, -40], 1.45),
  mistralClose: S(-12, 58, [60, 0, -40], 2.2),
  claudeAxon: S(44, 24, [-200, 60, 200], 1.0),
};
const frozenCodex = () => snap('codex-hold', (x) => withCam(CAM.wall, () => codexWorld(x, 12.3)));
const frozenClaude = () => snap('claude-end', (x) => withCam(CAM.claudeAxon, () => claudeWorld(x, 24.0)));
const frozenMistral = () => snap('mistral-end', (x) => withCam(CAM.mistral, () => mistralWorld(x, 32.4)));
function burn(ctx: CanvasRenderingContext2D, t: number) {
  const c = off('csrc'); withCam(CAM.claudeAxon, () => claudeWorld(ctx2(c), t));
  const k = prog(t, 24.9, 25.9), blk = Math.max(1, Math.round(lerp(1, 7, ein(k))));
  if (blk <= 1) blit(ctx, c, 1, 'copy'); else bitmapify(c, ctx, blk, PAL, 0.25, ss(0.2, 0.9, k));
  if (k > 0.55) { const m = off('mx'); mistralWorld(ctx2(m), t); blit(ctx, m, ss(0.55, 1, k)); }
}
// the hold: everything frozen, only the floor lattice re-indexes, and one counter ticks
function strandedHold(ctx: CanvasRenderingContext2D, f: number, f0: number, f1: number) {
  blit(ctx, frozenCodex(), 1, 'copy');
  const u = prog(f, f0, f1), r = new R(ctx); r.cam = build(CAM.wall);
  for (let k = -14; k <= 14; k++) {
    const x = k * 110 + 70 * Math.sin(u * 2.2 + k * 0.45), z = k * 110 + 60 * Math.cos(u * 1.7 + k * 0.6);
    r.line([x, -2, -1400], [x + 160 * Math.sin(u * 1.3), -2, 1200], '170,200,255', 1, 0.07);
    r.line([-1700, -2, z], [1700, -2, z + 120 * Math.cos(u * 1.1)], '170,200,255', 1, 0.05);
  }
  r.flush(true);
  const secs = Math.floor((f - f0) / FPS * 7) + 1;
  tag(ctx, 'session 1 · codex', 120, 110, 'rgba(214,232,255,0.7)');
  tag(ctx, `follow-up pending · ${String(Math.floor(secs / 60)).padStart(2, '0')}:${String(secs % 60).padStart(2, '0')}`, 120, 136, 'rgba(214,232,255,0.45)', 1, 13);
}

// ======================================================================= the rupture: three sessions re-indexed into one image
function rupture(ctx: CanvasRenderingContext2D, f: number, f0: number, f1: number) {
  const src = [frozenCodex(), frozenClaude(), frozenMistral()];
  const u = prog(f, f0, f1), step = Math.floor(f / 2);
  bg(ctx, '#000');
  const n = Math.round(lerp(5, 46, ein(clamp(u * 1.25)))), collapse = ss(0.82, 1, u);
  const amp = 30 + 260 * ss(0.15, 0.6, u);
  const r = rng(step * 31 + 7), edges: number[] = [0];
  for (let i = 1; i < n; i++) edges.push((i / n) * H + (r() - 0.5) * (H / n) * 0.8);
  edges.push(H);
  for (let i = 0; i < n; i++) {
    let y0 = edges[i], y1 = edges[i + 1];
    if (collapse > 0) { y0 = lerp(y0, H / 2, collapse); y1 = lerp(y1, H / 2, collapse); }
    if (y1 - y0 < 0.6) continue;
    const s = Math.floor(hash(i * 13.1 + Math.floor(step / 2)) * 3), dx = (r() - 0.5) * amp, sk = (r() - 0.5) * 0.12 * ss(0.3, 0.7, u);
    ctx.save(); ctx.beginPath(); ctx.rect(0, y0, W, y1 - y0); ctx.clip();
    ctx.setTransform(1, 0, sk, 1, dx - sk * (y0 + y1) / 2, (r() - 0.5) * 18 * u); ctx.drawImage(src[s], 0, 0); ctx.restore();
    if (i > 0) hair(ctx, 0, y0, W, y0, 0.55, 1, 0.8);
  }
  if (collapse > 0) hair(ctx, 0, H / 2, W, H / 2, collapse, 1, 1.2);
}
// held void: three session frames as ghost outlines in disagreeing coordinate systems; one wordmark
function voidHold(ctx: CanvasRenderingContext2D, f: number, f0: number, f1: number) {
  bg(ctx, '#020203'); LIGHT = false;
  const u = prog(f, f0, f1);
  for (let i = 0; i < 3; i++) {
    const rot = (i - 1) * 0.05 + Math.sin(u * 2 + i * 2.1) * 0.035, sk = Math.sin(u * 1.6 + i) * 0.08, s = 0.42 + i * 0.07 + 0.02 * Math.sin(u * 2.4 + i);
    ctx.save(); ctx.translate(W / 2 + (i - 1) * 40 * Math.cos(u * 1.3 + i), H / 2 + (i - 1) * 16); ctx.rotate(rot); ctx.transform(1, 0, sk, 1, 0, 0);
    ctx.save(); ctx.globalAlpha = 0.13; ctx.drawImage([frozenCodex(), frozenClaude(), frozenMistral()][i], -W * s / 2, -H * s / 2, W * s, H * s); ctx.restore();
    ctx.strokeStyle = `rgba(200,210,230,${0.28 + 0.07 * i})`; ctx.lineWidth = 1; ctx.strokeRect(-W * s / 2, -H * s / 2, W * s, H * s);
    for (let k = 1; k < 8; k++) { ctx.beginPath(); ctx.moveTo(-W * s / 2 + (W * s * k) / 8, -H * s / 2); ctx.lineTo(-W * s / 2 + (W * s * k) / 8, -H * s / 2 + 8); ctx.stroke(); }
    ctx.restore();
  }
  const blink = f - f0 === 46 || f - f0 === 47;
  txt(ctx, blink ? 'brωrd' : 'brnrd', W / 2, H / 2 - 6, blink ? `700 104px ${F.BODONI}` : `700 104px ${F.SANS}`, '#fff', 'center', 'middle', -2);
  // the fault: a hairline through the frame, broken where the word sits
  const gx = 230, y = H / 2 + 58;
  hair(ctx, 0, y, W / 2 - gx, y + 0.5, 0.75, 1); hair(ctx, W / 2 + gx, y - 2, W, y - 2.5, 0.75, 1);
  tag(ctx, 'session boundary: none', W / 2, H - 120, 'rgba(235,240,248,0.35)', ss(0.35, 0.45, u), 13, 'center');
}

// ======================================================================= the chain: one structure, many representations
const PROV = [{ n: 'CODEX', x: -620 }, { n: 'CLAUDE', x: 0 }, { n: 'MISTRAL', x: 620 }];
const BEAD_X = [-880, -760, -600, -110, 70, 500, 690, 880];
const TOP = S(0, 90, [0, 0, 0], 0.95), ISO = S(-45, 32, [0, 150, 0], 0.88);
function provBoxes(r: R, e: number, col: string, ink: boolean) {
  const h = 380 * e;
  PROV.forEach((p, i) => {
    r.box([p.x - 210, 0, -210], [p.x + 210, h, 210], col, 1.2, 0.9);
    // hatching on the roof (architect's poché)
    if (e > 0.05) for (let k = -200; k < 210; k += 28) r.line([p.x + k, h, -210], [p.x + Math.min(210, k + 120), h, -210 + Math.min(420, 120 + 210 - k) * 0], col, 1, 0.0);
    for (let k = -180; k <= 180; k += 36) r.line([p.x - 210, h, k], [p.x + 210, h, k], col, 1, 0.22);
    r.label(p.n, [p.x - 200, h, -228], `600 18px ${F.MONO}`, ink ? `rgba(22,20,18,0.9)` : 'rgba(235,240,248,0.85)', 1, 'left', 0, 0, 6);
    r.label(`0${i + 1}`, [p.x + 200, h, -228], `400 14px ${F.MONO}`, ink ? `rgba(22,20,18,0.5)` : 'rgba(235,240,248,0.5)', 1, 'right');
  });
}
function workLine(r: R, y: number, col: string, draw = 1, ink = false) {
  r.path([[-1000, y, 0], [1000, y, 0]], col, 2.4, 1, draw);
  BEAD_X.forEach((x, i) => { if ((x + 1000) / 2000 < draw) { r.dot([x, y, 0], 5, col, 1, 1); } });
  if (draw > 0.2) r.label('#482', [-1000, y, 0], `600 20px ${F.MONO}`, ink ? INK : '#fff', 1, 'left', 0, -18);
}
function faceAffine(r: R, tl: V3, tr: V3, bl: V3) {
  const A = r.P(tl), B = r.P(tr), D = r.P(bl);
  return [(B[0] - A[0]) / W, (B[1] - A[1]) / W, (D[0] - A[0]) / H, (D[1] - A[1]) / H, A[0], A[1]];
}
function diagram(ctx: CanvasRenderingContext2D, f: number, f0: number, fx: number, f1: number, dark: boolean, faces: number) {
  // f0..fx flat diagram, fx..f1 extrudes while the projection rebases top→axonometric
  const k = expo(prog(f, fx, fx + 10)), e = eout(prog(f, fx, fx + 14));
  LIGHT = !dark; bg(ctx, dark ? '#030304' : PAPER);
  const r = new R(ctx); r.cam = blend(TOP, ISO, k);
  const col = dark ? '220,228,240' : '22,20,18';
  // ground grid (draws in)
  const gd = eout(prog(f, f0, f0 + 8));
  for (let x = -1000; x <= 1000; x += 100) r.line([x, 0, -400], [x, 0, -400 + 800 * gd], col, 1, 0.08);
  provBoxes(r, Math.max(0.0001, e), col, !dark);
  workLine(r, 0, dark ? '255,255,255' : '22,20,18', eout(prog(f, f0 + 2, f0 + 12)), !dark);
  r.flush(dark);
  // provider environments occupying the faces of the same structure
  if (faces > 0 && e > 0.9) {
    const tex = [frozenCodex(), frozenClaude(), frozenMistral()];
    PROV.forEach((p, i) => {
      const h = 380 * e, m = faceAffine(r, [p.x - 210, h, 210], [p.x + 210, h, 210], [p.x - 210, 0, 210]);
      ctx.save(); ctx.globalAlpha = faces; ctx.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]); ctx.drawImage(tex[i], 0, 0); ctx.restore();
      const m2 = faceAffine(r, [p.x - 210, h, -210], [p.x - 210, h, 210], [p.x - 210, 0, -210]);
      ctx.save(); ctx.globalAlpha = faces * 0.55; ctx.setTransform(m2[0], m2[1], m2[2], m2[3], m2[4], m2[5]); ctx.drawImage(tex[(i + 1) % 3], 0, 0); ctx.restore();
    });
    const r2 = new R(ctx); r2.cam = r.cam; workLine(r2, 0, '255,255,255', 1); provBoxes(r2, 1, '235,240,248', false); r2.flush(true);
  }
  return r.cam;
}
// terminal rows; the #482 token sits in one column, so later it becomes a riser through every machine
const ROWS = [
  ['$ brnrd ls', ''],
  ['seat    claude · opus      ', '#482', '  retry on 409        live'],
  ['strand  codex · gpt-6      ', '#482', '  tests               done'],
  ['strand  mistral · medium   ', '#482', '  release v2.4.1      live'],
  ['host    m1 · fra-1 · ci    ', '#482', '  same thread         ·'],
];
const TX = 150, TY = 290, LH = 116, TFONT = 44;
function terminal(ctx: CanvasRenderingContext2D, f: number, f0: number) {
  bg(ctx, '#000'); LIGHT = false;
  const font = `500 ${TFONT}px ${F.MONO}`;
  ROWS.forEach((row, i) => {
    const age = f - f0 - i * 3; if (age < 0) return;
    const y = TY + i * LH;
    if (row.length === 2) { txt(ctx, row[0].slice(0, Math.min(row[0].length, age * 4)), TX, y, font, 'rgba(255,255,255,0.6)', 'left'); return; }
    txt(ctx, row[0], TX, y, font, 'rgba(235,240,248,0.85)', 'left');
    const x1 = TX + measure(ctx, row[0], font), w1 = measure(ctx, row[1], font);
    ctx.fillStyle = '#fff'; ctx.fillRect(x1 - 6, y - 26, w1 + 12, 52);
    txt(ctx, row[1], x1, y, font, '#000', 'left');
    txt(ctx, row[2], x1 + w1, y, font, 'rgba(235,240,248,0.85)', 'left');
  });
  if ((f >> 2) % 2 === 0) { ctx.fillStyle = '#fff'; ctx.fillRect(TX, TY + 5 * LH - 24, 20, 46); }
}
// rows as slabs: front-on it is the terminal; rebased, it is a rack of machines
const MACH = ['m1 · laptop', 'fra-1 · vps', 'ci · runner', 'nas · homelab'];
const FRONT = S(0, 0, [0, 0, 0], 1);
function slabs(ctx: CanvasRenderingContext2D, f: number, fA: number, fB: number, toIso: number, depthK: number, labelK: number, fault: number) {
  bg(ctx, '#030304'); LIGHT = false;
  const r = new R(ctx);
  const isoM = S(-38, 26, [0, -40, -200], 0.98, 0.25);
  r.cam = blend(FRONT, isoM, toIso);
  const font = `500 ${TFONT}`;
  const x0 = TX - W / 2, xTok = (() => { const c = ctx; return x0 + measure(c, ROWS[1][0], `${font}px ${F.MONO}`); })();
  const wTok = measure(ctx, '#482', `${font}px ${F.MONO}`);
  const D = 300 * depthK;
  if (fault > 0) { const P = r.P([xTok + wTok / 2, 0, 0]); r.faults = [{ x: P[0], y: P[1] + 200 * Math.sin(f * 0.21), a: 0.5 + f * 0.05, shift: 16 + 40 * fault, gap: 3, shear: 0.06 }]; }
  for (let i = 1; i < ROWS.length; i++) {
    const yc = H / 2 - (TY + i * LH), y0 = yc - 36, y1 = yc + 36;
    r.box([x0 - 30, y0, -D], [x0 + 1440, y1, 0], '200,212,232', 1.1, 0.75);
    r.poly([[x0 - 30, y1, -D], [x0 + 1440, y1, -D], [x0 + 1440, y1, 0], [x0 - 30, y1, 0]], 'rgb(200,212,232)', 0.05);
    for (let k = 1; k < 9; k++) r.line([x0 + 1440, y0 + 8, -D * k / 9], [x0 + 1440, y1 - 8, -D * k / 9], '200,212,232', 1, 0.3);
    r.text3d(ROWS[i][0] + '      ' + ROWS[i][2], [x0, yc - 12, 0.5], [1, 0, 0], [0, 1, 0], `${font}px ${F.MONO}`, 'rgba(235,240,248,0.85)', 1 - 0.5 * toIso);
    r.poly([[xTok - 6, yc - 26, 1], [xTok + wTok + 6, yc - 26, 1], [xTok + wTok + 6, yc + 26, 1], [xTok - 6, yc + 26, 1]], '#ffffff', 1);
    if (labelK > 0) r.text3d(MACH[i - 1], [x0, y1 + 1, -D + 40], [1, 0, 0], [0, 0, 1], `600 30px ${F.SANS}`, 'rgba(255,255,255,0.9)', labelK);
  }
  // the riser: same work, every machine
  if (depthK > 0.3) {
    const ya = H / 2 - (TY + LH) + 60, yb = H / 2 - (TY + 4 * LH) - 60;
    r.path([[xTok + wTok / 2, ya + 80, -D / 2], [xTok + wTok / 2, yb - 80, -D / 2]], '255,255,255', 2.4, 1, ss(0.3, 1, depthK));
    for (let i = 1; i < 5; i++) r.dot([xTok + wTok / 2, H / 2 - (TY + i * LH), -D / 2], 5, '255,255,255', ss(0.5, 1, depthK), 1);
  }
  r.flush(true);
  // token text drawn last so it stays crisp on the white chip
  const r2 = new R(ctx); r2.cam = r.cam; r2.faults = r.faults;
  for (let i = 1; i < ROWS.length; i++) r2.text3d('#482', [xTok, H / 2 - (TY + i * LH) - 12, 1.5], [1, 0, 0], [0, 1, 0], `${font}px ${F.MONO}`, '#000', 1);
  r2.flush(false);
  if (fault > 0 && r.faults.length) fracture(ctx, r.faults[0], f / FPS, 0.5 * fault, 700);
  return r.cam;
}
// repos in plan: four lanes, the #482 branch drawn in white
const LANES = ['brnrd', 'site', 'infra', 'notebook'];
const LZ = [-390, -130, 130, 390];
const BR: V3[] = [[-700, 0, -390], [-560, 0, -300], [380, 0, -300], [520, 0, -390]];
function repos(ctx: CanvasRenderingContext2D, f: number, c: Cam, draw: number, label: number) {
  bg(ctx, '#030304'); LIGHT = false;
  const r = new R(ctx); r.cam = c;
  LANES.forEach((n, i) => {
    r.path([[-1000, 0, LZ[i]], [1000, 0, LZ[i]]], '190,205,230', 2, 0.85, draw);
    for (let k = 0; k < 14; k++) { const x = -940 + k * 140 + (hash(i * 7 + k) - 0.5) * 60; if ((x + 1000) / 2000 < draw) r.dot([x, 0, LZ[i]], 3, '170,185,210', 0.8); }
    if (i > 0 && hash(i) > 0.3) { const a = -600 + hash(i + 3) * 600; r.path([[a, 0, LZ[i]], [a + 80, 0, LZ[i] - 60], [a + 420, 0, LZ[i] - 60]], '170,185,210', 1, 0.45, draw); }
    r.label(n, [-1000, 0, LZ[i]], `500 18px ${F.MONO}`, 'rgba(235,240,248,0.7)', label, 'left', 0, -14, 2);
  });
  r.path(BR, '255,255,255', 4, 1, draw); r.path(BR, '255,255,255', 14, 0.12, draw);
  BEAD_X.slice(1, 7).forEach((x, i) => { const xx = lerp(-480, 300, i / 5); if ((xx + 1000) / 2000 < draw) r.dot([xx, 0, -300], 5, '255,255,255', 1, 1); });
  r.label('#482', [-560, 0, -300], `600 30px ${F.MONO}`, '#fff', label, 'left', 0, -22);
  r.flush(true); bloom(ctx, 0.6, 10);
  return r;
}
// the branch made physical: an extruded rail with sleepers, read down its length
const HASHES = ['a1e07c3', '5d2f9b0', 'c88e114', '0b7a6de', '3f9a1c2', 'e41b7d0', '9c2a5f1', 'd70e3b8'];
const TL_LABELS = ['commit', 'commit', 'limit', 'carried', 'rebuilt', 'seat', 'strand', 'v2.4.1'];
function rail(ctx: CanvasRenderingContext2D, f: number, c: Cam, flat: number) {
  bg(ctx, '#040405'); LIGHT = false;
  const r = new R(ctx); r.cam = c;
  const L = 4200, G = 70;
  for (let k = -12; k <= 12; k++) r.line([-L, -1, k * 120], [L, -1, k * 120], '120,130,150', 1, 0.06 * (1 - flat));
  // sleepers
  for (let i = 0; i < 28; i++) {
    const x = -L + 150 + i * 300; r.box([x - 22, 0, -150], [x + 22, 14, 150], '160,170,190', 1, 0.6 * (1 - flat));
    r.poly([[x - 22, 14, -150], [x + 22, 14, -150], [x + 22, 14, 150], [x - 22, 14, 150]], '#3a3f4a', 0.9 * (1 - flat));
  }
  // rail heads: lit top, dark web
  for (const z of [-G, G]) {
    r.poly([[-L, 14, z - 10], [L, 14, z - 10], [L, 14, z + 10], [-L, 14, z + 10]], '#202329', 1);
    r.poly([[-L, 44, z - 7], [L, 44, z - 7], [L, 44, z + 7], [-L, 44, z + 7]], '#e8ecf4', 1);
    r.line([-L, 44, z - 7], [L, 44, z - 7], '255,255,255', 1.2, 0.9); r.line([-L, 14, z], [L, 44, z], '120,130,150', 1, 0.0);
  }
  // the work: beads become stations
  BEAD_X.forEach((bx, i) => {
    const x = bx * 2.4 * (1 - 0.0 * flat);
    r.dot([x, 44, 0], 6, '255,255,255', 1, 1);
    r.text3d(HASHES[i], [x + 30, 15, 110], [1, 0, 0], [0, 0, -1], `500 34px ${F.MONO}`, 'rgba(235,240,248,0.85)', 1 - flat);
  });
  r.flush(true);
  return r;
}
function timeline(ctx: CanvasRenderingContext2D, r: R, f: number, f0: number) {
  // labels on the now-flat rail: the branch has become a process timeline
  BEAD_X.forEach((bx, i) => {
    const P = r.P([bx * 2.4, 44, 0]), age = f - f0 - i * 2; if (age < 0) return;
    const up = i % 2 === 0 ? -1 : 1;
    ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,0.8)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(P[0], P[1] + up * 14); ctx.lineTo(P[0], P[1] + up * 70); ctx.stroke(); ctx.restore();
    txt(ctx, TL_LABELS[i], P[0], P[1] + up * 92, `600 26px ${F.SANS}`, i === 7 ? '#fff' : 'rgba(235,240,248,0.9)', 'center', 'middle', 0, 1);
    txt(ctx, HASHES[i], P[0], P[1] + up * 122, `400 14px ${F.MONO}`, 'rgba(235,240,248,0.45)', 'center', 'middle', 1);
  });
}

// ======================================================================= manifesto
const BIG = (n: number) => `800 ${n}px ${F.SANS}`;
function typeLine(ctx: CanvasRenderingContext2D, parts: { s: string; font: string; col: string; img?: string }[], x: number, y: number) {
  let cx = x;
  for (const p of parts) {
    if (p.img) { logo(ctx, p.img, cx + 70, y - 52, 150, p.col === '#fff' ? '#ffffff' : null, p.img === 'clawd'); cx += 170; continue; }
    ctx.font = p.font; ctx.fillStyle = p.col; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillText(p.s, cx, y); cx += ctx.measureText(p.s).width;
  }
  return cx;
}
const MODELS: { s: string; font: string; col: string; img?: string }[] = [
  { s: 'MODEL', font: `500 150px ${F.MONO}`, col: `rgb(${C.blue})` },
  { s: 'model', font: `italic 400 186px ${F.SERIF}`, col: `rgb(${C.orange})` },
  { s: '', font: '', col: '', img: 'codex' },
  { s: 'MODEL', font: `400 122px ${F.PIX}`, col: `rgb(${C.amber})` },
  { s: 'MODEL', font: `400 170px ${F.ANTON}`, col: '#fff' },
  { s: '', font: '', col: '', img: 'clawd' },
  { s: 'MODEL', font: `900 150px ${F.DOTO}`, col: '#fff' },
  { s: 'Model', font: `700 160px ${F.BODONI}`, col: '#fff' },
];
function workLineFlat(ctx: CanvasRenderingContext2D, x0: number, x1: number, y: number, k: number, a = 1) {
  hair(ctx, x0, y, x0 + (x1 - x0) * k, y, a, 0.6, 2.2);
  const cols = [C.blue, C.blue, C.blue, '232,140,105', '232,140,105', C.amber, C.amber, '255,255,255'];
  for (let q = 0; q < 8; q++) { const x = x0 + 40 + q * ((x1 - x0 - 80) / 7); if (x > x0 + (x1 - x0) * k) break; ctx.save(); ctx.globalAlpha = a; ctx.fillStyle = `rgb(${cols[q]})`; ctx.fillRect(x - 5, y - 5, 10, 10); ctx.restore(); }
}

// ======================================================================= the edit list
// [first frame, last frame] inclusive; fn(ctx, f) draws the whole frame.
type Shot = [number, number, (ctx: CanvasRenderingContext2D, f: number) => void];
const black = (ctx: CanvasRenderingContext2D) => { bg(ctx, '#000'); LIGHT = false; };
const hairFrame = (ctx: CanvasRenderingContext2D, f: number) => { bg(ctx, '#000'); LIGHT = false; hair(ctx, 0, H / 2, W, H / 2, 1, 1, 1.2); };
const A = (m: Medium) => (ctx: CanvasRenderingContext2D, f: number) => art(ctx, m, f);
const M = (i: number, w = 1500, v = false) => (ctx: CanvasRenderingContext2D) => mark(ctx, i, w, v);

const EDL: Shot[] = [
  // ---- A. cold open: #482 through eight media in 21 frames, then the hairline becomes main
  [0, 2, hairFrame],
  [3, 5, A('print')], [6, 7, A('pixel')], [8, 9, A('crop')], [10, 12, A('halftone')],
  [13, 15, A('term')], [16, 17, (ctx) => { bg(ctx, '#05070d'); LIGHT = false; logo(ctx, 'codex', W / 2, H / 2, 520, '#ffffff'); }],
  [18, 20, A('doto')], [21, 21, black],
  // ---- B. codex does useful work: flat diagram → dimensional → close, hard-cut framings, no tour
  [22, 60, (ctx, f) => { LIGHT = false; withCam(CAM.codexFlat, () => codexWorld(ctx, mapT(f, 22, 60, 1.75, 4.1))); lift(ctx, 0.6);
    if (f === 40 || f === 41) { invert(ctx); LIGHT = true; }
    if (f >= 47 && f <= 48) { bg(ctx, '#000'); txt(ctx, 'c88e114', W * 0.3, H / 2, `500 600px ${F.MONO}`, '#fff'); }
    if (f >= 52 && f <= 55) { bracket(ctx, 760, 420, 400, 240, 'rgba(214,232,255,0.9)'); tag(ctx, '+0:26', 770, 410, 'rgba(214,232,255,0.9)', 1, 16); } }],
  [61, 62, (ctx, f) => { LIGHT = false; withCam(CAM.codexFlat, () => codexWorld(ctx, 4.1)); txt(ctx, '3f9a1c2', W / 2, H / 2, `500 ${fit(ctx, '3f9a1c2', (n) => `500 ${n}px ${F.MONO}`, 1500)}px ${F.MONO}`, 'rgba(214,232,255,0.92)'); }],
  [63, 96, (ctx, f) => { LIGHT = false; withCam(CAM.codexAxon, () => codexWorld(ctx, mapT(f, 63, 96, 4.2, 7.5))); lift(ctx, 0.6);
    if (f >= 68 && f <= 70) { const c = layer('cx', (x) => x.drawImage(ctx.canvas, 0, 0)); xerox(ctx, c, 0.3, [22, 20, 18], [236, 232, 223], f, 0.01, 2, true); LIGHT = true; } if (f >= 92) bracket(ctx, 560, 330, 760, 330, 'rgba(214,232,255,0.9)', 1); }],
  [97, 99, A('outline')], 
  [100, 136, (ctx, f) => { LIGHT = false; withCam(CAM.codexClose, () => codexWorld(ctx, mapT(f, 100, 136, 7.7, 10.2))); lift(ctx, 0.6); if (f === 114 || f === 115) { const c = layer('cx', (x) => x.drawImage(ctx.canvas, 0, 0)); xerox(ctx, c, 0.25, [214, 232, 255], [6, 10, 20], f, 0.01, 4, true); } if (f >= 124 && f <= 127) tag(ctx, 'follow-up: retry on 409', 1180, 700, 'rgba(214,232,255,0.9)', 1, 20); }],
  [137, 138, (ctx) => { // the deliberately raw frame: an unstyled error page, crushed
    bg(ctx, '#fff'); LIGHT = true; txt(ctx, '429 Too Many Requests', 40, 60, `bold 34px Times, serif`, '#000', 'left', 'top');
    txt(ctx, 'usage limit reached. try again in 4h 52m.', 40, 120, `16px Times, serif`, '#000', 'left', 'top'); ctx.fillStyle = '#00e'; ctx.fillRect(40, 160, 220, 1); crush(ctx, 3); }],
  [139, 139, (ctx) => { bg(ctx, '#e8f0ff'); LIGHT = true; }],
  [140, 150, (ctx, f) => { LIGHT = false; withCam(CAM.wall, () => codexWorld(ctx, mapT(f, 140, 150, 10.42, 10.95))); }],
  // ---- C. HOLD 1 — stranded (3.3 s). Nothing moves but the floor lattice and one counter.
  [151, 250, (ctx, f) => { LIGHT = false; strandedHold(ctx, f, 151, 250); }],
  // ---- D. carried by hand: copy of a copy, each generation loses lines
  [251, 253, (ctx) => { bg(ctx, '#000'); LIGHT = false; txt(ctx, '⌘C', W / 2, H / 2 + 30, `400 ${700}px ${F.ANTON}`, '#fff'); }],
  [254, 266, (ctx, f) => { LIGHT = false; blit(ctx, frozenCodex(), 1, 'copy'); carry(ctx, mapT(f, 254, 266, 14.2, 15.3)); }],
  [267, 270, (ctx, f) => { const c = layer('cp', (x) => { x.drawImage(frozenCodex(), 0, 0); carry(x, 15.75); }); xerox(ctx, c, 0.42, [22, 20, 18], [236, 232, 223], f, 0.02, 2, true); LIGHT = true; tag(ctx, 'copy 1', 120, 120, INK, 1); }],
  [271, 276, (ctx, f) => { LIGHT = false; blit(ctx, frozenCodex(), 1, 'copy'); carry(ctx, mapT(f, 271, 276, 15.8, 16.25)); }],
  [277, 279, (ctx, f) => { const c = layer('cp', (x) => { x.drawImage(frozenCodex(), 0, 0); carry(x, 16.3); }); halftone(ctx, c, 9, INK, PAPER, true, 1.6); LIGHT = true; tag(ctx, 'copy 2 · 3/6 lines', 120, 120, INK, 1); }],
  [280, 282, (ctx) => { bg(ctx, IVORY); LIGHT = true; txt(ctx, '⌘V', W / 2, H / 2 + 30, `400 700px ${F.ANTON}`, INK); }],
  [283, 295, (ctx, f) => { LIGHT = false; blit(ctx, frozenCodex(), 1, 'copy'); carry(ctx, mapT(f, 283, 295, 16.6, 17.95)); }],
  // ---- E. claude: the page; words stand up (flat → dimensional by hard cut)
  [296, 297, (ctx) => { bg(ctx, IVORY); LIGHT = true; logo(ctx, 'clawd', W / 2, H / 2, 560, INK, true); }],
  [298, 330, (ctx, f) => { LIGHT = true; withCam(CAM.claudeFlat, () => claudeWorld(ctx, mapT(f, 298, 330, 18.0, 18.75))); }],
  [331, 333, (ctx) => { bg(ctx, IVORY); LIGHT = true; txt(ctx, '≈', W / 2, H / 2 - 40, `italic 400 900px ${F.SERIF}`, `rgb(${C.orange})`); tag(ctx, 'context, reconstructed', W / 2, H - 140, INK, 0.8, 18, 'center'); }],
  [334, 396, (ctx, f) => { LIGHT = true; withCam(CAM.claudeAxon, () => claudeWorld(ctx, mapT(f, 334, 396, 18.85, 24.0))); if (f >= 372 && f <= 375) bracket(ctx, 700, 380, 520, 260, 'rgba(35,28,22,0.9)', 1);
    if (f >= 356 && f <= 358) { const c = layer('cl', (x) => x.drawImage(ctx.canvas, 0, 0)); halftone(ctx, c, 10, INK, IVORY, false, 1.4); } }],
  // ---- F. burn to bitmap; a fresh session picks a head with no memory, releases the wrong lineage
  [397, 405, (ctx, f) => { LIGHT = false; burn(ctx, mapT(f, 397, 405, 24.9, 25.9)); }],
  [406, 431, (ctx, f) => { LIGHT = false; withCam(CAM.mistral, () => mistralWorld(ctx, mapT(f, 406, 431, 26.4, 28.7))); lift(ctx, 0.35); if (f === 420 || f === 421) crush(ctx, 14); }],
  [432, 459, (ctx, f) => { LIGHT = false; withCam(CAM.mistralClose, () => mistralWorld(ctx, mapT(f, 432, 459, 28.85, 30.45))); lift(ctx, 0.35); if (f >= 445 && f <= 446) { invert(ctx); } }],
  [460, 461, (ctx) => { bg(ctx, '#0c0303'); LIGHT = false; txt(ctx, 'HEAD?', W / 2, H / 2, `400 ${fit(ctx, 'HEAD?', (n) => `400 ${n}px ${F.PIX}`, 1500)}px ${F.PIX}`, 'rgb(255,190,80)'); }],
  [462, 470, (ctx, f) => { LIGHT = false; withCam(CAM.mistral, () => mistralWorld(ctx, mapT(f, 462, 470, 30.65, 31.3))); }],
  [471, 473, (ctx) => { bg(ctx, '#ffbe46'); LIGHT = true; txt(ctx, 'v2.4.0', W / 2, H / 2, `400 ${fit(ctx, 'v2.4.0', (n) => `400 ${n}px ${F.PIX}`, 1600)}px ${F.PIX}`, '#1a0503'); }],
  [474, 500, (ctx, f) => { LIGHT = false; withCam(CAM.mistral, () => mistralWorld(ctx, mapT(f, 474, 500, 31.45, 32.6))); if (f >= 488 && f <= 491) tag(ctx, '#482 missing', 1220, 820, 'rgb(235,50,35)', 1, 22); }],
  // ---- G. three sessions, no continuity — substitutions accelerating
  [501, 503, (ctx) => { blit(ctx, frozenCodex(), 1, 'copy'); LIGHT = false; }], [504, 506, (ctx) => { blit(ctx, frozenClaude(), 1, 'copy'); LIGHT = true; }], [507, 509, (ctx) => { blit(ctx, frozenMistral(), 1, 'copy'); LIGHT = false; }],
  [510, 511, (ctx) => { blit(ctx, frozenCodex(), 1, 'copy'); }], [512, 513, (ctx) => { blit(ctx, frozenClaude(), 1, 'copy'); }], [514, 515, (ctx) => { blit(ctx, frozenMistral(), 1, 'copy'); }],
  [516, 516, (ctx) => { blit(ctx, frozenCodex(), 1, 'copy'); invert(ctx); }], [517, 517, (ctx) => { blit(ctx, frozenClaude(), 1, 'copy'); invert(ctx); }], [518, 518, (ctx) => { blit(ctx, frozenMistral(), 1, 'copy'); invert(ctx); }],
  [519, 523, (ctx) => { bg(ctx, '#000'); LIGHT = false; tag(ctx, '3 sessions   1 work   0 memory', W / 2, H / 2 + 8, '#fff', 1, 28, 'center'); }],
  [524, 529, black],
  // ---- H. brnrd breaks the session boundary
  [530, 561, (ctx, f) => { LIGHT = false; rupture(ctx, f, 530, 561); }],
  [562, 564, M(0)], [565, 566, M(1)], [567, 568, M(2)], [569, 570, M(3)], [571, 573, M(4)], [574, 576, M(5, 0, true)], [577, 578, M(6)], [579, 580, M(7)],
  [581, 581, black],
  // ---- HOLD 2 — the void (3.3 s): three frames that no longer agree, one word
  [582, 680, (ctx, f) => voidHold(ctx, f, 582, 680)],
  // ---- I. the chain: one structure, re-represented, never toured
  [681, 724, (ctx, f) => { diagram(ctx, f, 681, 704, 724, false, 0); if (f >= 692 && f <= 695) { tag(ctx, 'one work', 300, 330, INK, 1, 18); bracket(ctx, 250, 470, 1420, 140, 'rgba(22,20,18,0.8)'); } }],
  [725, 726, (ctx, f) => { diagram(ctx, f, 681, 704, 724, false, 0); invert(ctx); LIGHT = false; }],
  [727, 762, (ctx, f) => {
    const c = diagram(ctx, f, 681, 704, 724, true, ss(727, 733, f));
    // the resident passes as a fault: inside the band the next representation already holds
    if (f >= 744) {
      const u = prog(f, 744, 762), bx = lerp(-300, W + 300, eio(u)), ang = 0.32, bw = 180;
      const nxt = layer('next', (x) => terminal(x, 800, 763));
      const pts = [[bx - bw, -50], [bx + bw, -50], [bx + bw - Math.tan(ang) * (H + 100), H + 50], [bx - bw - Math.tan(ang) * (H + 100), H + 50]];
      ctx.save(); ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.clip(); ctx.drawImage(nxt, 14, -6); ctx.restore();
      hair(ctx, pts[0][0], pts[0][1], pts[3][0], pts[3][1], 0.9, 1.2); hair(ctx, pts[1][0], pts[1][1], pts[2][0], pts[2][1], 0.9, 1.2);
    }
    LIGHT = false; return c;
  }],
  [763, 790, (ctx, f) => terminal(ctx, f, 763)],
  [791, 803, (ctx, f) => { slabs(ctx, f, 791, 803, expo(prog(f, 793, 803)), eout(prog(f, 791, 801)), 0, 0); }],
  [804, 806, (ctx) => { bg(ctx, '#000'); LIGHT = false; tag(ctx, 'same seat · other machine', W / 2, H / 2, '#fff', 1, 30, 'center'); }],
  [807, 842, (ctx, f) => { slabs(ctx, f, 807, 842, 1, 1, ss(807, 814, f), ss(818, 832, f)); lift(ctx, 0.5); }],
  // blocks reproject as repo topology: a six-frame cascade through a permuted axis frame, then plan
  [843, 848, (ctx, f) => {
    const isoM = S(-38, 26, [0, -40, -200], 0.98, 0.25), mid = S(70, 60, [0, 0, 0], 0.5, 0, 25, 'yxz'), plan = S(0, 90, [-60, 0, -40], 0.86);
    const u = prog(f, 843, 848) * 2, c = u < 1 ? blend(isoM, mid, expo(u)) : blend(mid, plan, expo(u - 1));
    if (u < 1) slabs(ctx, f, 0, 0, 1, 1, 1, 1); else repos(ctx, f, c, 1, 0);
    if (u < 1) { const cc = c; withCam(cc, () => {}); }
  }],
  [849, 884, (ctx, f) => { const r = repos(ctx, f, build(S(0, 90, [-60, 0, -40], 0.86)), 1, ss(849, 853, f)); if (f >= 866 && f <= 869) { const P = r.P([-100, 0, -300]); bracket(ctx, P[0] - 520, P[1] - 60, 1040, 120, '#fff'); } }],
  // the branch line becomes a physical rail (hard cut, perspective), then turns 90° into a process timeline
  [885, 886, (ctx) => { bg(ctx, '#fff'); LIGHT = true; hair(ctx, 0, H / 2, W, H / 2, 1, 1, 3, true); }],
  [887, 914, (ctx, f) => { const c = build(S(-62, 17, [lerp(-900, -500, prog(f, 887, 914)), 40, 0], 1.5, 0.8, 0, 'xyz', 1300)); rail(ctx, f, c, 0); lift(ctx, 0.5); }],
  [915, 924, (ctx, f) => {
    const a = build(S(-62, 17, [-500, 40, 0], 1.5, 0.8, 0, 'xyz', 1300)), b = build(S(0, 90, [0, 44, 0], 0.36, 0, 0)), k = expo(prog(f, 915, 924));
    rail(ctx, f, camLerp(a, b, k), k);
  }],
  [925, 962, (ctx, f) => { const r = rail(ctx, f, build(S(0, 90, [0, 44, 0], 0.36, 0, 0)), 1); lift(ctx, 0.5); timeline(ctx, r, f, 925); }],
  // typography becomes geometry: the timeline ticks stretch into bars, the bars read as a barcode
  [963, 972, (ctx, f) => {
    bg(ctx, '#000'); LIGHT = false; const k = expo(prog(f, 963, 970));
    for (let i = 0; i < 64; i++) { const x = 140 + i * 26 + (hash(i) - 0.5) * 8, w = 2 + Math.floor(hash(i * 3.3) * 4) * 3, h = lerp(14, H * 0.9, k * (0.5 + 0.5 * hash(i * 1.7))); ctx.fillStyle = '#fff'; ctx.fillRect(x, H / 2 - h / 2, w, h); }
  }],
  [973, 975, A('barcode')],
  [976, 977, A('thermal')],
  [978, 978, black],
  // ---- J. manifesto
  [979, 1043, (ctx, f) => {
    bg(ctx, '#010102'); LIGHT = false; const l = f - 979;
    const cyc = l >= 10 && l < 50 ? MODELS[Math.floor(l / 3) % MODELS.length] : { s: 'MODEL', font: BIG(150), col: '#f2f4f8' };
    ctx.save(); (ctx as any).letterSpacing = '-5px';
    typeLine(ctx, [{ s: 'THE ', font: BIG(150), col: '#f2f4f8' }, cyc], 260, 500);
    typeLine(ctx, [{ s: 'IS REPLACEABLE.', font: BIG(150), col: '#f2f4f8' }], 260, 650); ctx.restore();
    if (l < 8) tear(ctx, { x: W / 2, y: 575, a: -0.05, shift: 320 * (1 - eout(l / 8)), gap: 10 * (1 - l / 8) }, f / FPS, 1);
    if (l > 58) tear(ctx, { x: W / 2, y: 575, a: -0.05, shift: 260 * ein(prog(l, 58, 64)) }, f / FPS, 1);
  }],
  [1044, 1047, hairFrame],
  // HOLD 3 — the work (3.6 s): dead still; the lineage underneath re-indexes, slowly
  [1048, 1156, (ctx, f) => {
    bg(ctx, '#010102'); LIGHT = false; const u = prog(f, 1048, 1156);
    ctx.save(); ctx.strokeStyle = 'rgba(200,210,230,0.07)'; ctx.lineWidth = 1;
    for (let k = 0; k < 22; k++) { const y = 80 + k * 44 + 14 * Math.sin(u * 2.5 + k * 0.5); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y + 30 * Math.sin(u * 1.4 + k)); ctx.stroke(); }
    ctx.restore();
    ctx.save(); (ctx as any).letterSpacing = '-5px'; typeLine(ctx, [{ s: 'THE WORK', font: BIG(150), col: '#fff' }], 260, 500); typeLine(ctx, [{ s: 'IS YOURS.', font: BIG(150), col: '#fff' }], 260, 650); ctx.restore();
    workLineFlat(ctx, 260, 1660, 740, eio(prog(f, 1050, 1080)));
    tag(ctx, '#482 · codex → claude → mistral → any', 260, 790, 'rgba(235,240,248,0.5)', ss(1095, 1100, f), 15);
  }],
  [1157, 1158, A('print')],
  [1159, 1195, (ctx, f) => {
    bg(ctx, '#000'); LIGHT = false; const l = f - 1159;
    const lines = ['OWN', 'YOUR', 'AGENTS.'];
    lines.forEach((s, i) => { const fam = (n: number) => `400 ${n}px ${F.ANTON}`; txt(ctx, s, W / 2, 190 + i * 350, fam(Math.min(330, fit(ctx, s, fam, 1700))), '#fff', 'center', 'middle', 4); });
    if (l >= 14 && l < 18) { const c = layer('own', (x) => x.drawImage(ctx.canvas, 0, 0)); halftone(ctx, c, 12, '#000', '#fff', false, 1.2); LIGHT = true; }
    if (l >= 24 && l < 28) { tag(ctx, 'resident · not a provider session', 120, 1020, 'rgba(255,255,255,0.85)', 1, 16); bracket(ctx, 110, 40, 1700, 1000, '#fff'); }
    tear(ctx, { x: W / 2, y: H / 2 + 20, a: -0.04, shift: 14 + 4 * Math.sin(f * 0.3) + (l < 5 ? 300 * (1 - l / 5) : 0), gap: 3, wob: 2 }, f / FPS, 1);
  }],
  [1196, 1197, M(2)], [1198, 1199, M(1)], [1200, 1201, M(4)], [1202, 1203, M(3)],
  [1204, 1244, (ctx, f) => {
    bg(ctx, '#000'); LIGHT = false; const l = f - 1204;
    txt(ctx, 'brnrd', W / 2, H / 2 - 10, `700 150px ${F.SANS}`, '#fff', 'center', 'middle', -4, 1);
    hair(ctx, 0, H / 2 + 90, W / 2 - 260, H / 2 + 90, 0.8, 1); hair(ctx, W / 2 + 260, H / 2 + 88, W, H / 2 + 88, 0.8, 1);
    tag(ctx, 'brnrd.dev', W / 2, H / 2 + 150, 'rgba(235,240,248,0.5)', ss(1214, 1220, f), 18, 'center');
    if (l >= 22 && l <= 23) { bg(ctx, '#000'); txt(ctx, 'b^_^d', W / 2, H / 2 - 10, `500 150px ${F.MONO}`, '#fff'); }
  }],
  [1245, 1259, black],
];

export function drawCut(ctx: CanvasRenderingContext2D, fo: number) {
  const f = srcFrame(fo);
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over'; ctx.filter = 'none';
  OVR.cam = null;
  const s = EDL.find(([a, b]) => f >= a && f <= b);
  if (!s) { black(ctx); return; }
  s[2](ctx, f);
}
export function finishCut(ctx: CanvasRenderingContext2D, fo: number) {
  const f = srcFrame(fo);
  ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
  grain(ctx, f / FPS, LIGHT ? 0.05 : 0.075, !LIGHT);
  vignette(ctx, LIGHT ? 0.18 : 0.5, LIGHT ? '60,40,20' : '0,0,0');
}
export const EDL_SPANS = EDL.map(([a, b]) => [a, b]);
