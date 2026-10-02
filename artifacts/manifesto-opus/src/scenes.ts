import {
  W, H, V3, R, cam, view, PERM, Cam, camLerp, clamp, lerp, prog, ss, eio, eout, ein, expo, win, hash, rng, vlerp, off, ctx2,
  bloom, bitmapify, tear, fracture, resident, grain, vignette, Fault,
} from './engine';
import { F } from './fonts';

// ---------- camera keyframing ----------
type Spec = { yaw: number; pitch: number; roll: number; t: V3; zoom: number; persp: number; D: number; perm: string };
const S = (yaw: number, pitch: number, t: V3, zoom: number, persp = 0, roll = 0, perm = 'xyz', D = 1800): Spec => ({ yaw, pitch, roll, t, zoom, persp, D, perm });
const build = (s: Spec): Cam => cam(view(s.yaw, s.pitch, s.roll, PERM[s.perm]), s.t, s.zoom, s.persp, s.D);
function blend(a: Spec, b: Spec, k: number): Cam {
  if (a.perm === b.perm)
    return build({ yaw: lerp(a.yaw, b.yaw, k), pitch: lerp(a.pitch, b.pitch, k), roll: lerp(a.roll, b.roll, k), t: vlerp(a.t, b.t, k), zoom: Math.exp(lerp(Math.log(a.zoom), Math.log(b.zoom), k)), persp: lerp(a.persp, b.persp, k), D: lerp(a.D, b.D, k), perm: a.perm });
  return camLerp(build(a), build(b), k); // non-rigid: the coordinate system itself shears through the swap
}
type Key = [number, Spec];
function track(t: number, keys: Key[]): Cam {
  if (t <= keys[0][0]) return build(keys[0][1]);
  for (let i = 1; i < keys.length; i++) if (t <= keys[i][0]) return blend(keys[i - 1][1], keys[i][1], eio(prog(t, keys[i - 1][0], keys[i][0])));
  return build(keys[keys.length - 1][1]);
}
const C = { blue: '120,170,255', ice: '214,232,255', steel: '110,126,150', ink: '35,28,22', orange: '217,119,87', sepia: '150,125,100', amber: '255,150,40', red: '235,50,35', white: '235,240,248', warm: '240,226,205', violet: '175,150,255' };
function spec(h: number, l = 0.72): string {
  const a = 1 * Math.min(l, 1 - l), f = (n: number) => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); };
  return `${Math.round(f(0) * 255)},${Math.round(f(8) * 255)},${Math.round(f(4) * 255)}`;
}
export const HEADS: Record<string, [number, number]> = {};

// ---------- the work: two lineages, same coordinates in every world ----------
const A_X = [-340, -220, -100, 20, 140], AZ = -180, CT = [2.3, 2.85, 3.3, 3.65, 3.95];
const B_X = [-60, 90, 240], BZ = 140;
const HASHES = ['a1e07c3', '5d2f9b0', 'c88e114', '0b7a6de', '3f9a1c2'];
const LIMIT = 10.62, SX = 225;
const aPath = (x: number): V3[] => [[-1000, 0, 0], [-560, 0, 0], [-470, 0, -150], [-440, 0, AZ], [x, 0, AZ]];
const bPath = (x: number): V3[] => [[-1000, 0, 0], [-200, 0, 0], [-140, 0, 110], [-120, 0, BZ], [x, 0, BZ]];
function tipX(t: number) {
  if (t < CT[0]) return lerp(-440, A_X[0], prog(t, 2.0, CT[0]));
  for (let i = 1; i < 5; i++) if (t < CT[i]) return lerp(A_X[i - 1], A_X[i], prog(t, CT[i - 1], CT[i]));
  return lerp(A_X[4], 300, eout(prog(t, 7.7, LIMIT + 0.4)) * 0.75);
}

// =============== CODEX: cold, blue, measured ===============
const CODEX_KEYS: Key[] = [
  [1.2, S(0, 0, [-150, 0, -90], 0.95)],
  [4.9, S(-30, 33, [-120, 0, -110], 1.05)],
  [10.3, S(-36, 27, [40, 0, -130], 1.2)],
  [11.0, S(-36, 27, [40, 0, -130], 1.2)],
  [13.6, S(-44, 19, [200, 90, -170], 1.6)],
  [15.8, S(-26, 58, [-80, 0, -60], 0.5)],
];
export function codexWorld(ctx: CanvasRenderingContext2D, t: number) {
  const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, '#010309'); g.addColorStop(1, '#07101d');
  ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1; ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  const r = new R(ctx); r.cam = track(t, CODEX_KEYS);
  const hit = t >= LIMIT, tl = Math.min(t, LIMIT);
  if (t > LIMIT && t < LIMIT + 0.8) { const k = Math.pow(1 - prog(t, LIMIT, LIMIT + 0.8), 2); r.cam.cx += (hash(t * 97.3) - 0.5) * 50 * k; r.cam.cy += (hash(t * 41.7) - 0.5) * 50 * k; }
  const gA = ss(1.5, 4.2, t), LC = hit ? C.steel : C.blue;
  for (let x = -1600; x <= 1600; x += 80) r.line([x, 0, -1200], [x, 0, 1000], LC, 1, 0.15 * gA * (1 - Math.abs(x) / 1750) * (x % 400 === 0 ? 2.2 : 1));
  for (let z = -1200; z <= 1000; z += 80) r.line([-1600, 0, z], [1600, 0, z], LC, 1, 0.15 * gA * (1 - Math.abs(z + 100) / 1300) * (z % 400 === 0 ? 2.2 : 1));
  // glass hall: columns, beams, a scan plane that halts at the limit
  for (let x = -1600; x <= 1600; x += 320) for (const z of [-1200, -880, 900]) r.line([x, 0, z], [x, 1100, z], LC, 1, 0.2 * gA);
  for (const y of [275, 550, 825, 1100]) for (const z of [-1200, 900]) r.line([-1600, y, z], [1600, y, z], LC, 1, 0.1 * gA);
  const sy = (tl * 150) % 1100;
  for (const z of [-1200, -880]) r.line([-1600, sy, z], [1600, sy, z], C.ice, 1, (hit ? 0.2 : 0.5) * gA);
  for (let x = -1600; x <= 1600; x += 320) r.line([x, sy, -1200], [x, sy, -880], C.ice, 1, 0.25 * gA);
  // data dashes riding the floor lines — they freeze in place at the limit
  for (let k = 0; k < 34; k++) {
    const z = -1200 + 80 * Math.floor(hash(k) * 27), x = -1600 + ((tl * (300 + 300 * hash(k + 3)) + hash(k + 9) * 3200) % 3200);
    r.line([x, 0, z], [x + 50, 0, z], hit ? C.steel : C.ice, 1.3, 0.7 * gA);
  }
  // main + lineage A
  r.path([[-1600, 0, 0], [1600, 0, 0]], C.ice, 1.2, 0.5, ss(0.3, 1.6, t));
  r.label('main', [-1000, 0, 0], `400 14px ${F.MONO}`, `rgba(${C.ice},0.5)`, ss(0.8, 1.4, t), 'left', 0, -14);
  const tx = tipX(tl);
  const pre = aPath(-440);
  r.path(pre, C.ice, 1.8, 0.95, prog(t, 1.4, 2.0));
  if (t > 2.0) {
    const live = Math.min(tx, SX);
    r.line([-440, 0, AZ], [live, 0, AZ], hit ? C.steel : C.ice, 1.8, 0.95);
    if (!hit) {
      // the follow-up tip, working
      if (t > 7.7) { const sc = ((t * 0.9) % 1) * 220; r.path([[tx, 0, AZ], [tx + 220, 0, AZ]], C.blue, 1, 0.35 * (0.6 + 0.4 * Math.sin(t * 20)), 1, 8); r.dot([tx + sc, 0, AZ], 2.2, C.ice, 0.8); }
      r.dot([tx, 0, AZ], 3, C.ice, 1);
    } else {
      // severed: the far side of the wall goes dead and sags
      const sag = -10 * ein(prog(t, LIMIT, LIMIT + 1.2));
      r.path([[SX + 4, sag, AZ], [tx, sag, AZ]], '220,90,100', 1.4, 0.8, 1, 6);
      r.dot([tx, sag, AZ], 3, '220,90,100', 0.8);
    }
  }
  r.label('feat/offline-sync', [-430, 0, AZ], `500 14px ${F.MONO}`, `rgba(${C.ice},0.8)`, ss(2.1, 2.6, t), 'left', 8, 22);
  A_X.forEach((x, i) => {
    if (t < CT[i]) return;
    const pop = eout(prog(t, CT[i], CT[i] + 0.3));
    r.dot([x, 0, AZ], 4.5, hit ? '170,185,210' : C.ice, 1, 1);
    r.dot([x, 0, AZ], 5 + 22 * pop, C.ice, (1 - pop) * 0.9, 2);
    r.label(HASHES[i], [x, 0, AZ], `400 12px ${F.MONO}`, `rgba(${C.ice},0.6)`, pop, 'center', 0, -16);
  });
  // dimension line: precise, measured
  const dA = ss(4.2, 4.9, t) * (hit ? 0.5 : 1);
  if (dA > 0) {
    const z = AZ - 80;
    r.path([[A_X[0], 0, z], [A_X[4], 0, z]], C.blue, 1, 0.8 * dA, prog(t, 4.2, 4.9));
    A_X.forEach((x, i) => { r.line([x, 0, z - 12], [x, 0, z + 12], C.blue, 1, 0.8 * dA); r.line([x, 0, z + 12], [x, 0, AZ - 8], C.blue, 1, 0.25 * dA); if (i) r.label(['+0:41', '+0:33', '+0:26', '+0:19'][i - 1], [(x + A_X[i - 1]) / 2, 0, z], `400 11px ${F.MONO}`, `rgba(${C.blue},0.9)`, dA, 'center', 0, 16); });
  }
  // PR: a sealed volume around the lineage
  const pb = ss(5.0, 6.6, t);
  if (pb > 0) {
    const a: V3 = [-400, 0, AZ - 55], b: V3 = [190, 95, AZ + 55];
    r.box(a, b, hit ? C.steel : C.ice, 1.1, 0.85, pb);
    r.poly([[a[0], b[1], a[2]], [b[0], b[1], a[2]], [b[0], b[1], b[2]], [a[0], b[1], b[2]]], `rgb(${C.blue})`, 0.06 * pb);
    const la = ss(6.2, 6.6, t);
    r.label('PR #482', [a[0], b[1], a[2]], `500 22px ${F.MONO}`, `rgba(${C.ice},1)`, la, 'left', 0, -14);
    r.label('feat/offline-sync → main', [a[0], b[1], a[2]], `400 12px ${F.MONO}`, `rgba(${C.ice},0.55)`, la, 'left', 0, -40);
    ['tests', 'lint', 'build'].forEach((s, i) => r.label('✓ ' + s, [b[0], b[1], a[2]], `400 13px ${F.MONO}`, `rgba(${C.ice},0.85)`, ss(6.8 + i * 0.2, 7.0 + i * 0.2, t), 'left', 16, -30 + i * 18));
  }
  // the wall
  if (t > LIMIT - 0.22) {
    const yo = 1500 * (1 - ein(prog(t, LIMIT - 0.22, LIMIT))), y0 = yo, y1 = yo + 780, z0 = -820, z1 = 520;
    r.poly([[SX, y0, z0], [SX, y0, z1], [SX, y1, z1], [SX, y1, z0]], '#0a1322', 0.62);
    r.path([[SX, y0, z0], [SX, y0, z1], [SX, y1, z1], [SX, y1, z0], [SX, y0, z0]], C.ice, 1.6, 0.95);
    for (let k = 0; k < 44; k++) { const za = z0 + k * 46; const zb = Math.min(z1, za + (y1 - y0)); r.line([SX, y0, za], [SX, y0 + (zb - za), zb], C.ice, 1, 0.1); }
    r.text3d('USAGE LIMIT', [SX, y0 + 520, -640], [0, 0, 1], [0, 1, 0], `600 128px ${F.MONO}`, `rgb(${C.ice})`, 0.97);
    r.text3d('resets in 4h 52m', [SX, y0 + 440, -636], [0, 0, 1], [0, 1, 0], `400 46px ${F.MONO}`, `rgba(${C.ice},0.75)`, ss(LIMIT + 0.3, LIMIT + 0.8, t));
    if (hit) { // debris off the impact
      const dt = t - LIMIT, rr = rng(7);
      for (let k = 0; k < 60; k++) { const vx = (rr() - 0.5) * 500, vy = 200 + rr() * 500, vz = (rr() - 0.5) * 900, z = z0 + rr() * (z1 - z0); const y = vy * dt - 700 * dt * dt; if (y < -5) continue; r.dot([SX + vx * dt, y, z + vz * dt * 0.3], 1.6, C.ice, 0.8 * (1 - prog(dt, 0, 1.4))); }
    }
  }
  const hp = r.P([Math.min(tx, SX), 0, AZ]); HEADS.codex = [hp[0], hp[1]];
  r.flush(true);
  bloom(ctx, 0.85, 14);
  if (hit && t < LIMIT + 0.3) { ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.fillStyle = `rgba(200,220,255,${0.55 * (1 - prog(t, LIMIT, LIMIT + 0.3))})`; ctx.fillRect(0, 0, W, H); ctx.restore(); }
}

// =============== CARRY: context copied by hand, losing lines ===============
const SLIP: [string, string][] = [['branch', 'feat/offline-sync'], ['pr', '#482'], ['todo', 'retry on 409'], ['note', 'keep ledger order'], ['last', 'c5 · 3f9a1c2'], ['state', 'tests red on 409']];
const DROP = [0, 0, 0, 15.0, 15.4, 15.75];
function carry(ctx: CanvasRenderingContext2D, t: number) {
  if (t < 13.7 || t > 18.3) return;
  const rr = new R(ctx); rr.cam = track(13.7, CODEX_KEYS); const p0 = rr.P([200, 0, AZ]);
  const k1 = eio(prog(t, 13.7, 16.1)), k2 = ein(prog(t, 16.3, 17.95));
  const bez = (a: number, b: number, c: number, u: number) => (1 - u) * (1 - u) * a + 2 * (1 - u) * u * b + u * u * c;
  let x = bez(p0[0], p0[0] + 160, 1200, k1), y = bez(p0[1], 150, 440, k1);
  let sc = lerp(0.25, 1, eout(prog(t, 13.7, 14.5))), rot = lerp(-0.14, 0.05, k1);
  x = lerp(x, W / 2, k2); y = lerp(y, H / 2, k2); sc *= Math.exp(Math.log(7.2) * k2); rot = lerp(rot, 0, k2);
  // darken the world behind the slip
  ctx.save(); ctx.fillStyle = `rgba(1,3,8,${0.55 * ss(14.6, 16.2, t)})`; ctx.fillRect(0, 0, W, H); ctx.restore();
  // a hairline tether back to the wall, thinning out
  if (t < 16.4) { ctx.save(); ctx.strokeStyle = `rgba(${C.ice},${0.5 * (1 - prog(t, 15.2, 16.4))})`; ctx.setLineDash([3, 6]); ctx.beginPath(); ctx.moveTo(p0[0], p0[1]); ctx.quadraticCurveTo(p0[0] + 160, 150, x, y); ctx.stroke(); ctx.restore(); }
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(sc, sc);
  const w = 400, h = 250;
  ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 40 * (1 - k2); ctx.shadowOffsetY = 18 * (1 - k2);
  ctx.fillStyle = '#EEE6D6'; ctx.fillRect(-w / 2, -h / 2, w, h); ctx.shadowColor = 'transparent';
  ctx.strokeStyle = 'rgba(150,125,100,0.25)'; ctx.lineWidth = 0.6;
  for (let i = 0; i < 7; i++) { ctx.beginPath(); ctx.moveTo(-w / 2 + 14, -h / 2 + 50 + i * 30); ctx.lineTo(w / 2 - 14, -h / 2 + 50 + i * 30); ctx.stroke(); }
  ctx.strokeStyle = `rgba(${C.orange},0.6)`; ctx.beginPath(); ctx.moveTo(-w / 2 + 70, -h / 2 + 8); ctx.lineTo(-w / 2 + 70, h / 2 - 8); ctx.stroke();
  ctx.font = `500 11px ${F.MONO}`; ctx.fillStyle = 'rgba(35,28,22,0.5)'; ctx.fillText('context.txt — pasted', -w / 2 + 16, -h / 2 + 24);
  const fade = 1 - ss(17.3, 17.95, t);
  SLIP.forEach(([k, v], i) => {
    let dy = 0, dr = 0, a = fade, jx = 0;
    if (DROP[i]) { const u = prog(t, DROP[i], DROP[i] + 1.1); dy = 700 * ein(u); dr = (i % 2 ? 1 : -1) * 0.9 * ein(u); a *= 1 - ss(0.4, 1, u); if (t > DROP[i] - 0.18 && t < DROP[i]) jx = (hash(t * 91 + i) - 0.5) * 14; }
    if (a <= 0) return;
    ctx.save(); ctx.translate(-w / 2 + 16 + jx, -h / 2 + 46 + i * 30 + dy); ctx.rotate(dr); ctx.globalAlpha = a;
    ctx.font = `400 12px ${F.MONO}`; ctx.fillStyle = 'rgba(35,28,22,0.45)'; ctx.fillText(k, 0, 0);
    ctx.font = `500 15px ${F.MONO}`; ctx.fillStyle = i < 3 ? '#231c16' : `rgb(${C.orange})`; ctx.fillText(v, 62, 0);
    ctx.restore();
  });
  ctx.restore();
}

// =============== CLAUDE: warm ivory, typographic architecture ===============
const CLAUDE_KEYS: Key[] = [
  [17.9, S(0, 90, [-120, 0, 560], 0.62)],
  [20.5, S(34, 30, [-110, 0, 230], 0.6)],
  [24.4, S(44, 24, [0, 0, 60], 0.72)],
  [26.4, S(52, 21, [60, 0, 0], 0.82)],
];
const WORDS: { s: string; o: V3; font: (n: number) => string; n: number; d: number }[] = [
  { s: 'feat/offline-sync', o: [-900, 0, 900], font: (n) => `400 ${n}px ${F.SERIF}`, n: 190, d: 18.8 },
  { s: '#482', o: [-900, 0, 600], font: (n) => `400 ${n}px ${F.SERIF}`, n: 360, d: 19.15 },
  { s: 'retry on 409', o: [-900, 0, 360], font: (n) => `italic 400 ${n}px ${F.SERIF}`, n: 230, d: 19.5 },
];
const B_T = [20.8, 21.9, 23.0];
export function claudeWorld(ctx: CanvasRenderingContext2D, t: number) {
  ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1; ctx.fillStyle = '#EDE5D4'; ctx.fillRect(0, 0, W, H);
  const r = new R(ctx); r.cam = track(t, CLAUDE_KEYS);
  // the sheet: ruled, margined, finite
  r.poly([[-1300, 0, -900], [1300, 0, -900], [1300, 0, 1000], [-1300, 0, 1000]], '#F4EEE2', 0.9);
  for (let z = -880; z <= 980; z += 44) r.line([-1300, 0, z], [1300, 0, z], C.sepia, 1, 0.16);
  r.line([-820, 0, -900], [-820, 0, 1000], C.orange, 1.3, 0.45);
  r.path([[-1300, 0, -900], [1300, 0, -900], [1300, 0, 1000], [-1300, 0, 1000], [-1300, 0, -900]], C.sepia, 1, 0.35);
  // words stand up off the page and become the architecture
  WORDS.forEach((wd) => {
    const th = (Math.PI / 2) * eio(prog(t, wd.d, wd.d + 1.5));
    const v: V3 = [0, Math.sin(th), Math.cos(th)], n: V3 = [0, -Math.cos(th), Math.sin(th)];
    const ext = ss(wd.d + 0.8, wd.d + 2.0, t);
    for (let k = 12; k >= 1; k--) r.text3d(wd.s, [wd.o[0], wd.o[1] + n[1] * k * 4, wd.o[2] + n[2] * k * 4], [1, 0, 0], v, wd.font(wd.n), `rgba(${C.orange},1)`, 0.07 * ext);
    r.text3d(wd.s, wd.o, [1, 0, 0], v, wd.font(wd.n), '#231c16', 0.92);
  });
  // lineage A as a ghost: what really happened, unknown here
  r.path(aPath(A_X[4]), C.sepia, 1.2, 0.45, 1, 10);
  A_X.forEach((x) => r.dot([x, 0, AZ], 5, C.sepia, 0.5, 3));
  r.label('c5 ?', [A_X[4], 0, AZ], `italic 400 30px ${F.SERIF}`, `rgba(${C.sepia},0.9)`, ss(19.6, 20.2, t), 'left', 14, 26);
  // lineage B: rebuilt from what survived the copy
  const bd = prog(t, 20.2, 23.2);
  const bx = lerp(-200, 330, bd);
  r.path([[-1300, 0, 0], [1300, 0, 0]], C.ink, 1.3, 0.55);
  if (bd > 0) r.path(bPath(bx), C.ink, 2.2, 0.95, clamp(bd * 3));
  B_X.forEach((x, i) => {
    if (t < B_T[i]) return; const pop = eout(prog(t, B_T[i], B_T[i] + 0.35));
    r.dot([x, 0, BZ], 7 + 16 * (1 - pop), C.orange, 1, 3); r.dot([x, 0, BZ], 3.5, C.ink, 1);
    r.label(['c6′', 'c7′', 'c8′'][i], [x, 0, BZ], `italic 400 30px ${F.SERIF}`, `rgba(${C.ink},0.85)`, pop, 'center', 0, -22);
  });
  const ca = ss(20.6, 21.4, t);
  if (ca > 0) {
    const m: V3 = [(A_X[4] + B_X[0]) / 2, 0, (AZ + BZ) / 2];
    r.path([[A_X[4], 0, AZ], m, [B_X[0], 0, BZ]], C.orange, 1.2, 0.8 * ca, prog(t, 20.6, 21.4), 7);
    r.label('≈ context', m, `italic 400 28px ${F.SERIF}`, `rgba(${C.orange},1)`, ca, 'left', 14, 8);
  }
  r.label('follow-up, done.', [B_X[2], 0, BZ], `italic 400 34px ${F.SERIF}`, `rgba(${C.orange},1)`, ss(23.5, 24.0, t), 'left', 22, 10);
  const hp = r.P([B_X[2], 0, BZ]); HEADS.claude = [hp[0], hp[1]];
  r.flush(false);
  grain(ctx, t, 0.07, false);
}

// =============== MISTRAL: amber / red bitmap ===============
const PAL = [[12, 4, 4], [52, 8, 6], [128, 20, 14], [214, 46, 24], [255, 120, 30], [255, 184, 70], [255, 238, 200], [52, 84, 190], [140, 172, 255]];
const MISTRAL_KEYS: Key[] = [
  [25.4, S(-30, 30, [-60, 0, -20], 0.95)],
  [29.0, S(-22, 34, [40, 0, -20], 0.9)],
  [30.8, S(-26, 36, [140, 20, -100], 1.2)],
  [31.4, S(-26, 36, [140, 20, -100], 1.2)],
  [34.2, S(-36, 40, [60, 0, 0], 0.85)],
];
const JUMPS: [number, number][] = [[28.9, 1], [29.4, 0], [29.7, 1], [29.9, 0], [30.05, 1], [30.17, 0], [30.26, 1], [30.36, 0]];
const REL = 30.95;
function mistralCam(t: number) {
  const c = track(t, MISTRAL_KEYS);
  if (t > REL && t < REL + 0.6) { const k = Math.pow(1 - prog(t, REL, REL + 0.6), 2); c.cx += (hash(t * 77) - 0.5) * 40 * k; c.cy += (hash(t * 33) - 0.5) * 40 * k; }
  return c;
}
export function mistralWorld(ctx: CanvasRenderingContext2D, t: number, block = 6) {
  const src = off('msrc'), sc = ctx2(src);
  sc.globalCompositeOperation = 'source-over'; sc.globalAlpha = 1; sc.fillStyle = '#0c0303'; sc.fillRect(0, 0, W, H);
  const r = new R(sc); r.cam = mistralCam(t);
  for (let x = -1600; x <= 1600; x += 100) r.line([x, 0, -1200], [x, 0, 1100], C.red, 2, 0.35);
  for (let z = -1200; z <= 1100; z += 100) r.line([-1600, 0, z], [1600, 0, z], C.red, 2, 0.35);
  // stepped bitmap skyline, quantised heights
  for (let i = 0; i < 34; i++) {
    const x = -1600 + i * 96, z = 520 + (i % 3) * 140;
    const hq = 60 * Math.max(1, Math.floor(1 + 7 * hash(i) + 3 * Math.sin(Math.floor(t * 8) * 0.7 + i)));
    r.box([x, 0, z], [x + 70, hq, z + 70], C.amber, 2, 0.55);
    r.poly([[x, hq, z], [x + 70, hq, z], [x + 70, hq, z + 70], [x, hq, z + 70]], `rgb(${C.amber})`, 0.25);
  }
  r.path([[-1600, 0, 0], [1600, 0, 0]], '255,220,180', 3, 0.7);
  const ap = prog(t, 26.5, 27.8), bp = prog(t, 27.2, 28.6);
  r.path(aPath(A_X[4]), '120,150,255', 5, 1, ap);
  r.path(bPath(B_X[2]), C.amber, 5, 1, bp);
  A_X.forEach((x, i) => { if (ap > (i + 4) / 9) r.dot([x, 0, AZ], 10, '150,180,255', 1, 1); });
  const strobe = t > 31.5 && Math.floor(t * 30 / 3) % 2 === 0;
  B_X.forEach((x, i) => { if (bp > 0.55 + i * 0.15) r.dot([x, 0, BZ], 10, strobe ? C.red : '255,190,80', 1, 1); });
  // the cursor: a fresh session choosing a head
  let cur = -1, cp = 0;
  for (let i = 0; i < JUMPS.length; i++) if (t >= JUMPS[i][0]) { cur = JUMPS[i][1]; cp = prog(t, JUMPS[i][0], JUMPS[i][0] + 0.06); }
  if (cur >= 0 && t < REL + 0.2) {
    const hA: V3 = [A_X[4], 0, AZ], hB: V3 = [B_X[2], 0, BZ];
    const p = cur === 0 ? hA : hB, s = 50 + 30 * (1 - cp);
    if (Math.floor(t * 30 / 2) % 2 === 0 || t > 30.4) r.box([p[0] - s, 0, p[2] - s], [p[0] + s, 16, p[2] + s], '255,240,200', 3, 1);
  }
  // the release block drops onto lineage A
  let yo = 0;
  if (t > REL - 0.25) {
    yo = 900 * (1 - ein(prog(t, REL - 0.25, REL)));
    const g = t > 32.4 ? Math.floor(hash(Math.floor(t * 15)) * 3) * 10 : 0;
    const a: V3 = [70 + g, yo, -250], b: V3 = [210 + g, 150 + yo, -110];
    r.poly([[a[0], a[1], a[2]], [b[0], a[1], a[2]], [b[0], b[1], a[2]], [a[0], b[1], a[2]]], '#ffbe46', 1);
    r.poly([[a[0], b[1], a[2]], [b[0], b[1], a[2]], [b[0], b[1], b[2]], [a[0], b[1], b[2]]], '#ffeec8', 1);
    r.poly([[b[0], a[1], a[2]], [b[0], a[1], b[2]], [b[0], b[1], b[2]], [b[0], b[1], a[2]]], '#d62e18', 1);
    r.box(a, b, '255,240,200', 3, 1);
  }
  r.flush(true);
  bitmapify(src, ctx, block, PAL, 0.22);
  // crisp pixel type on top
  const L = new R(ctx); L.cam = r.cam;
  L.label('#482', [A_X[4], 0, AZ], `400 26px ${F.PIX}`, 'rgb(150,180,255)', ss(27.6, 27.9, t), 'left', 36, 36);
  L.label('FOLLOW-UP', [B_X[2], 0, BZ], `400 24px ${F.PIX}`, 'rgb(255,190,80)', ss(28.4, 28.7, t), 'left', 30, -18);
  if (t > REL - 0.25) {
    L.text3d('v2.4.0', [84, yo + 50, -250], [1, 0, 0], [0, 1, 0], `400 36px ${F.PIX}`, '#1a0503', 1);
    L.label('RELEASE', [140, yo + 150, -250], `400 22px ${F.PIX}`, 'rgb(255,238,200)', 1, 'center', 0, -26);
  }
  if (t > 31.5) {
    L.label('NOT IN RELEASE', [B_X[1], 0, BZ], `400 26px ${F.PIX}`, `rgb(${C.red})`, strobe ? 1 : 0.55, 'center', 0, 52);
    L.path([[B_X[2], 0, BZ], [140, 0, -110]], C.red, 2, 0.8, ss(31.5, 31.9, t), 10);
  }
  const hp = L.P([140, 75, -180]); HEADS.mistral = [hp[0], hp[1]];
  L.flush(false);
}

// =============== TOPOLOGY: one system, four readings ===============
const PY = 260, EXT = 800;
type Mod = { p: V3; w: number; d: number; h: number };
const TOPO = (() => {
  const r = rng(42); const planes: { y: number; nodes: V3[]; mods: Mod[]; rails: V3[][] }[] = [];
  for (let i = 0; i < 4; i++) {
    const y = i * PY, nodes: V3[] = [], mods: Mod[] = [];
    for (let k = 0; k < 18; k++) { const x = Math.round(r() * 14 - 7) * 100, z = Math.round(r() * 14 - 7) * 100; nodes.push([x, y, z]); if (r() < 0.62) mods.push({ p: [x, y, z], w: 30 + Math.floor(r() * 50), d: 30 + Math.floor(r() * 50), h: 20 + Math.pow(r(), 2) * 210 }); }
    const rails: V3[][] = [];
    for (let k = 0; k < 12; k++) { const a = nodes[Math.floor(r() * 18)], b = nodes[Math.floor(r() * 18)]; rails.push([a, [b[0], y, a[2]], b]); }
    planes.push({ y, nodes, mods, rails });
  }
  const risers: V3[][] = [];
  for (let i = 0; i < 3; i++) for (let k = 0; k < 7; k++) { const n = planes[i].nodes[Math.floor(r() * 18)]; risers.push([n, [n[0], n[1] + PY, n[2]]]); }
  return { planes, risers };
})();
const RP: V3[] = [[-700, 0, -600], [100, 0, -600], [100, 0, -100], [100, 260, -100], [100, 260, 500], [-500, 260, 500], [-500, 520, 500], [-500, 520, -300], [400, 520, -300], [400, 780, -300], [400, 780, 400], [-200, 780, 400], [-200, 780, -500], [-200, 520, -500], [600, 520, -500], [600, 520, 600], [600, 260, 600], [-600, 260, 600], [-600, 260, 0], [-600, 0, 0], [700, 0, 0], [700, 0, 700]];
const RL: number[] = [0]; for (let i = 1; i < RP.length; i++) RL.push(RL[i - 1] + Math.hypot(RP[i][0] - RP[i - 1][0], RP[i][1] - RP[i - 1][1], RP[i][2] - RP[i - 1][2]));
const RTOT = RL[RL.length - 1];
function rpAt(s: number): V3 { s = clamp(s, 0, RTOT); for (let i = 1; i < RP.length; i++) if (RL[i] >= s) return vlerp(RP[i - 1], RP[i], (s - RL[i - 1]) / (RL[i] - RL[i - 1] || 1)); return RP[RP.length - 1]; }
const sAt = (t: number) => RTOT * Math.pow(prog(t, 46.0, 75.5), 0.8);
const SW = [54.5, 61.5, 68.5];
const readingAt = (t: number) => (t < SW[0] ? 0 : t < SW[1] ? 1 : t < SW[2] ? 2 : 3);
const PROV_COL = [C.blue, '232,140,105', C.amber, C.violet];
function chaseYaw(s: number) {
  const a = rpAt(s - 350), b = rpAt(s + 350); let dx = b[0] - a[0], dz = b[2] - a[2];
  if (Math.hypot(dx, dz) < 1) { dx = 1; dz = 1; }
  return (Math.atan2(dx, dz) * 180) / Math.PI;
}
function readingSpec(i: number, t: number, rp: V3): Spec {
  if (i === 0) return S(45 + (t - 47.5) * 2.4, 31, rp, 0.44);
  if (i === 1) return S(22 + (t - 54.5) * 1.6, 14, rp, 0.46, 0.25, 0, 'yxz');
  if (i === 2) return S(10 + (t - 61.5) * 3, 78, rp, 0.52, 0.15, (t - 61.5) * 1.5);
  // processes: chase, low, deep perspective; smooth the heading over time
  let yaw = 0, n = 0;
  for (let k = -3; k <= 3; k++) { yaw += chaseYaw(sAt(t + k * 0.15)); n++; }
  return S(yaw / n, 13, rp, 1.05, 1, 0, 'xyz', 1000);
}
function randSpec(seed: number, rp: V3): Spec {
  const r = rng(seed); const perms = Object.keys(PERM);
  return S(r() * 360, -20 + r() * 110, rp, 0.3 + r() * 0.6, r() * 0.8, (r() - 0.5) * 60, perms[Math.floor(r() * perms.length)], 1400);
}
// a dense cascade: the coordinate system flips through several projections in a few frames
function cascade(t: number, t0: number, t1: number, from: Spec, to: Spec, seed: number, rp: V3): Cam {
  const n = 5, u = prog(t, t0, t1) * n, j = Math.min(n - 1, Math.floor(u)), f = expo(clamp((u - j) * 1.6));
  const seq: Spec[] = [from, randSpec(seed, rp), randSpec(seed + 1, rp), randSpec(seed + 2, rp), randSpec(seed + 3, rp), to];
  return blend(seq[j], seq[j + 1], f);
}
function topoCam(t: number, rp: V3): Cam {
  for (let i = 0; i < SW.length; i++) {
    const a = SW[i] - 0.45, b = SW[i] + 0.35;
    if (t >= a && t < b) return cascade(t, a, b, readingSpec(i, a, rp), readingSpec(i + 1, b, rp), 100 + i * 10, rp);
  }
  return build(readingSpec(readingAt(t), t, rp));
}
const READ_NAMES = ['PROVIDERS', 'MACHINES', 'PROJECTS', 'PROCESSES'];
const PLANE_LABELS = [
  ['CODEX', 'CLAUDE', 'MISTRAL', 'ANTIGRAVITY'],
  ['m1 · laptop', 'fra-1 · vps', 'ci · runner', 'nas · homelab'],
  ['brnrd', 'site', 'infra', 'notebook'],
  ['seat', 'strand', 'await', 'gate'],
];
function planeFont(ri: number, i: number) {
  if (ri === 0) return [`500 64px ${F.MONO}`, `italic 400 84px ${F.SERIF}`, `400 56px ${F.PIX}`, `300 60px ${F.SANS}`][i];
  if (ri === 1) return `400 54px ${F.MONO}`;
  if (ri === 2) return `600 80px ${F.SANS}`;
  return `400 58px ${F.MONO}`;
}
function planeCol(ri: number, i: number, k: number, t: number) {
  if (ri === 0) return i === 3 ? spec((k * 23 + t * 40) % 360) : PROV_COL[i];
  if (ri === 1) return '170,190,215';
  if (ri === 2) return C.warm;
  return k % 7 === 0 ? spec((k * 37 + t * 60) % 360) : '215,225,240';
}

// panels: frozen frames of the three sessions, textured into the world
const PANEL_T: Record<string, number> = { codex: 12.4, claude: 23.9, mistral: 32.8 };
const PANEL_HEAD: Record<string, [number, number]> = {};
const panelDone = new Set<string>();
function panelTex(name: string) {
  const c = off('panel-' + name);
  if (!panelDone.has(name)) {
    const x = ctx2(c); const t = PANEL_T[name];
    if (name === 'codex') codexWorld(x, t); else if (name === 'claude') claudeWorld(x, t); else mistralWorld(x, t);
    PANEL_HEAD[name] = [...HEADS[name]] as [number, number]; panelDone.add(name);
  }
  return c;
}
type Pl = { c: V3; u: V3; v: V3 };
const SCAT: Record<string, Pl> = {
  codex: { c: [-1000, 420, 160], u: [460, 0, -150], v: [0, 262, 0] },
  claude: { c: [0, 300, -260], u: [480, 0, 0], v: [0, 270, 0] },
  mistral: { c: [1000, 420, 160], u: [460, 0, 150], v: [0, 262, 0] },
};
const NAMES = ['codex', 'claude', 'mistral'];
const plLerp = (a: Pl, b: Pl, k: number): Pl => ({ c: vlerp(a.c, b.c, k), u: vlerp(a.u, b.u, k), v: vlerp(a.v, b.v, k) });
function panelAffine(r: R, p: Pl) {
  const tl = r.P([p.c[0] - p.u[0] + p.v[0], p.c[1] - p.u[1] + p.v[1], p.c[2] - p.u[2] + p.v[2]]);
  const tr = r.P([p.c[0] + p.u[0] + p.v[0], p.c[1] + p.u[1] + p.v[1], p.c[2] + p.u[2] + p.v[2]]);
  const bl = r.P([p.c[0] - p.u[0] - p.v[0], p.c[1] - p.u[1] - p.v[1], p.c[2] - p.u[2] - p.v[2]]);
  return [(tr[0] - tl[0]) / W, (tr[1] - tl[1]) / W, (bl[0] - tl[0]) / H, (bl[1] - tl[1]) / H, tl[0], tl[1]];
}
const apply = (m: number[], x: number, y: number): [number, number] => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
const SCAT_SPEC = S(0, 8, [0, 330, 0], 0.62, 0.5, 0, 'xyz', 2400);

// resident screen path during the rupture
function resRupture(t: number, heads: [number, number][]): [number, number] {
  if (t < 40.3) { const k = eout(prog(t, 38.9, 40.3)); return [lerp(-90, 820, k), lerp(1060, 600, k)]; }
  if (t < 40.8) { const k = eio(prog(t, 40.3, 40.8)); return [lerp(820, 880, k), lerp(600, 560, k)]; }
  if (t < 41.8) {
    const P: [number, number][] = [[880, 560], [1260, 360], [620, 300], [1520, 720], [420, 780], [1120, 500], [heads[0][0], heads[0][1]]];
    const u = prog(t, 40.8, 41.8) * 6, j = Math.min(5, Math.floor(u)), f = expo(clamp((u - j) * 2));
    return [lerp(P[j][0], P[j + 1][0], f), lerp(P[j][1], P[j + 1][1], f)];
  }
  if (t < 44.2) { // along the thread through the three heads
    const u = eio(prog(t, 41.8, 44.0)) * 2, j = Math.min(1, Math.floor(u)), f = u - j;
    return [lerp(heads[j][0], heads[j + 1][0], f), lerp(heads[j][1], heads[j + 1][1], f)];
  }
  return [lerp(heads[2][0], W / 2, eio(prog(t, 44.2, 46.2))), lerp(heads[2][1], H / 2, eio(prog(t, 44.2, 46.2)))];
}

export function topoWorld(ctx: CanvasRenderingContext2D, t: number) {
  ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1;
  const g = ctx.createRadialGradient(W / 2, H * 0.55, 100, W / 2, H / 2, W * 0.75); g.addColorStop(0, '#07080d'); g.addColorStop(1, '#010102');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  const r = new R(ctx);
  const s = sAt(t), rp = rpAt(s), ri = readingAt(t);
  // camera: scatter → rupture cascade → topology readings
  const morph = eio(prog(t, 44.2, 47.2));
  let cm: Cam;
  if (t < 44.2) {
    cm = build(SCAT_SPEC);
    const drift = (t - 34) * 0.9; cm = build({ ...SCAT_SPEC, yaw: Math.sin(drift * 0.2) * 6, pitch: 8 + Math.sin(drift * 0.13) * 3 });
    if (t >= 40.8 && t < 41.8) cm = cascade(t, 40.8, 41.8, SCAT_SPEC, SCAT_SPEC, 7, SCAT_SPEC.t);
  } else if (t < 47.2) cm = blend(SCAT_SPEC, readingSpec(0, 47.2, rpAt(sAt(47.2))), morph);
  else cm = topoCam(t, rp);
  // collapse: the whole system folds into the resident
  const col = ein(prog(t, 75.6, 77.9));
  if (col > 0) { const zc = Math.max(0.0005, 1 - col); cm.zoom *= zc; cm.m = cm.m.map((v, i) => (i >= 3 && i < 6 ? v * (1 - col * 0.7) : v)); }
  r.cam = cm;
  // the resident's wake: a live fault that follows it through the topology
  const inTopo = t >= 46.0;
  if (inTopo) {
    let sw = 0; for (const x of SW) sw = Math.max(sw, 1 - Math.abs(t - x) / 0.5);
    const ang = t * 0.35 + Math.sin(t * 1.3) * 0.4;
    const P0 = r.P(rp);
    r.faults = [{ x: P0[0], y: P0[1], a: ang, shift: 9 + 160 * sw * sw, shear: 0.02 + 0.2 * sw, wob: 3 + 20 * sw, gap: 2 }];
    if (sw > 0.2) { const rr = rng(Math.floor(t * 30)); r.faults.push({ x: W / 2 + (rr() - 0.5) * 600, y: H / 2 + (rr() - 0.5) * 300, a: rr() * Math.PI, shift: 120 * sw * rr(), shear: 0.1, gap: 3 }); }
    if (t > 75.4 && t < 78.0) r.faults.push({ x: W / 2, y: H / 2, a: -0.2 + col, shift: 40 * col, shear: 0.05 });
    // bend the coordinate system near the swaps
    const bend = 0.9 * sw * sw * (SW.findIndex((x) => Math.abs(t - x) < 0.5) % 2 ? -1 : 1) + 0.05 * Math.sin(t * 0.7);
    if (Math.abs(bend) > 0.002) r.warp = (p) => { const dx = p[0] - rp[0], dy = p[1] - rp[1], dz = p[2] - rp[2], a = (bend * dz) / 1000, c = Math.cos(a), sn = Math.sin(a); return [rp[0] + dx * c - dy * sn, rp[1] + dx * sn + dy * c, p[2]]; };
  }
  const topoA = ss(44.6, 46.8, t) * (1 - ss(77.6, 78.2, t));
  // ---- topology geometry
  if (topoA > 0) {
    r.alpha = topoA;
    // vastness: a deep sparse floor and far ghost stacks
    for (let x = -4000; x <= 4000; x += 400) r.line([x, -700, -4000], [x, -700, 4000], '120,140,170', 1, 0.07);
    for (let z = -4000; z <= 4000; z += 400) r.line([-4000, -700, z], [4000, -700, z], '120,140,170', 1, 0.07);
    for (const [ox, oz] of [[-3400, -2200], [3200, -1800], [-2600, 2600], [3000, 2400]]) for (let i = 0; i < 4; i++) {
      const y = i * PY, e = EXT * 0.8; r.path([[ox - e, y, oz - e], [ox + e, y, oz - e], [ox + e, y, oz + e], [ox - e, y, oz + e], [ox - e, y, oz - e]], '150,170,200', 1, 0.16);
      TOPO.planes[i].mods.slice(0, 6).forEach((m) => r.box([ox + m.p[0] * 0.8, y, oz + m.p[2] * 0.8], [ox + m.p[0] * 0.8 + m.w, y + m.h, oz + m.p[2] * 0.8 + m.d], '150,170,200', 1, 0.12));
    }
    // corner elevation axes tying the stack
    for (const [x, z] of [[-EXT, -EXT], [EXT, -EXT], [EXT, EXT], [-EXT, EXT]]) {
      r.line([x, -120, z], [x, 3 * PY + 160, z], '200,210,230', 1, 0.3);
      for (let y = 0; y <= 3 * PY; y += 26) r.line([x - 10, y, z], [x + 10, y, z], '200,210,230', 1, 0.22);
    }
    TOPO.planes.forEach((pl, i) => {
      const y = pl.y, e = EXT, cc = planeCol(ri, i, 0, t);
      r.poly([[-e, y, -e], [e, y, -e], [e, y, e], [-e, y, e]], `rgb(${cc})`, 0.035);
      r.path([[-e, y, -e], [e, y, -e], [e, y, e], [-e, y, e], [-e, y, -e]], cc, 1.3, 0.75);
      for (let k = -7; k <= 7; k++) { r.line([k * 100, y, -e], [k * 100, y, e], planeCol(ri, i, k + 20, t), 1, 0.11); r.line([-e, y, k * 100], [e, y, k * 100], planeCol(ri, i, k + 40, t), 1, 0.11); }
      for (const [x, z] of [[-e, -e], [e, -e], [e, e], [-e, e]]) { r.line([x, y, z], [x + Math.sign(x) * 60, y, z], cc, 1, 0.6); r.line([x, y, z], [x, y, z + Math.sign(z) * 60], cc, 1, 0.6); }
      r.text3d(PLANE_LABELS[ri][i], [-e + 30, y + 2, -e + 40], [1, 0, 0], [0, 0, 1], planeFont(ri, i), `rgb(${cc})`, 0.9);
      pl.mods.forEach((m, k) => {
        const fl = ri === 0 && i === 3 ? 30 + 18 * Math.sin(t * 1.4 + k) : 0; // antigravity: modules float
        const hh = ri === 1 ? m.h * 0.6 + 40 : m.h;
        const a: V3 = [m.p[0] - m.w / 2, y + fl, m.p[2] - m.d / 2], b: V3 = [m.p[0] + m.w / 2, y + fl + hh, m.p[2] + m.d / 2];
        const mc = planeCol(ri, i, k, t);
        r.box(a, b, mc, 1, 0.55);
        r.poly([[a[0], b[1], a[2]], [b[0], b[1], a[2]], [b[0], b[1], b[2]], [a[0], b[1], b[2]]], `rgb(${mc})`, 0.1);
        if (ri === 1) for (let yy = a[1] + 12; yy < b[1]; yy += 12) r.path([[a[0], yy, a[2]], [b[0], yy, a[2]], [b[0], yy, b[2]]], mc, 1, 0.3);
        if (ri === 2 && k % 2 === 0) { // repositories: little commit graphs growing off each node
          const gx = m.p[0] + m.w / 2 + 10, gz = m.p[2];
          r.line([gx, y, gz], [gx + 160, y, gz], mc, 1, 0.6);
          r.path([[gx + 40, y, gz], [gx + 60, y, gz - 36], [gx + 130, y, gz - 36]], mc, 1, 0.5);
          for (let q = 0; q < 5; q++) r.dot([gx + 20 + q * 32, y, gz], 2.2, mc, 0.9);
        }
      });
      pl.rails.forEach((rl, k) => {
        r.path(rl, planeCol(ri, i, k + 3, t), 1, 0.3);
        const npz = ri === 3 ? 4 : 1;
        for (let q = 0; q < npz; q++) {
          const u = (t * (0.18 + 0.1 * hash(k + i * 20)) * (ri === 3 ? 2.5 : 1) + hash(k * 3 + q * 7 + i)) % 1;
          const p = r.pathEnd(rl, u), p2 = r.pathEnd(rl, Math.max(0, u - (ri === 3 ? 0.08 : 0.02)));
          r.line(p2, p, '255,255,255', 1.6, 0.9); r.dot(p, 2.2, '255,255,255', 1);
        }
      });
    });
    TOPO.risers.forEach((rs, k) => { r.path(rs, '200,215,240', 1, 0.35); const u = (t * 0.4 + hash(k + 99)) % 1; r.dot(r.pathEnd(rs, u), 2, '255,255,255', 0.9); });
    // the resident's route as a rail, and the work as a trail with beads coloured by the plane that made them
    r.path(RP, '200,215,240', 1, 0.18);
    const trailS = s;
    const pts: V3[] = [RP[0]]; for (let i = 1; i < RP.length && RL[i] < trailS; i++) pts.push(RP[i]); pts.push(rp);
    r.alpha = topoA * (inTopo ? 1 : 0);
    r.path(pts, '255,255,255', 2.4, 0.95); r.path(pts, '255,255,255', 7, 0.08);
    for (let q = 180; q < trailS; q += 300) {
      const p = rpAt(q), pi = Math.round(p[1] / PY), bc = PROV_COL[clamp(pi, 0, 3)];
      r.dot(p, 5, bc, 1, 1); r.dot(p, 9, bc, 0.6, 2);
    }
    r.label('feat/offline-sync', rp, `500 15px ${F.MONO}`, 'rgba(235,240,248,0.85)', win(t, 47.6, 52.5, 0.4, 0.6) + win(t, 70.5, 75.2, 0.4, 0.4), 'left', 46, 36);
    const rel = ss(73.0, 73.3, t) * (1 - ss(75.4, 75.8, t));
    if (rel > 0) {
      const pop = eout(prog(t, 73.0, 73.4));
      r.box([rp[0] - 50 - 60 * (1 - pop), rp[1], rp[2] - 50 - 60 * (1 - pop)], [rp[0] + 50 + 60 * (1 - pop), rp[1] + 90, rp[2] + 50 + 60 * (1 - pop)], '255,255,255', 1.4, rel);
      r.label('release v2.4.1', rp, `600 20px ${F.SANS}`, 'rgba(255,255,255,1)', rel, 'left', 46, -40);
      r.label('whole lineage · 9 commits · 3 models', rp, `400 13px ${F.MONO}`, 'rgba(235,240,248,0.7)', rel, 'left', 46, -18);
    }
    r.alpha = 1;
  }
  // ---- panels (scatter → rupture → laid down as planes)
  const panA = ss(33.9, 34.2, t) * (1 - ss(45.0, 46.8, t));
  const heads: [number, number][] = [];
  if (panA > 0) {
    const r2 = new R(ctx); r2.cam = r.cam;
    NAMES.forEach((nm, i) => {
      const tex = panelTex(nm);
      const k = eio(prog(t, 44.2 + i * 0.25, 46.4 + i * 0.25));
      const pl = plLerp(SCAT[nm], { c: [0, i * PY, 0], u: [EXT, 0, 0], v: [0, 0, EXT * 0.5625] }, k);
      let m = panelAffine(r2, pl);
      let texSrc: HTMLCanvasElement = tex;
      let a = panA * (nm === 'mistral' ? 1 : ss(34.6 + i * 0.3, 35.6 + i * 0.3, t));
      if (nm === 'mistral' && t < 35.1) { // the live mistral frame shrinks into its panel
        const live = off('mlive'); mistralWorld(ctx2(live), Math.min(t, 34.3)); texSrc = live;
        const q = eio(prog(t, 33.9, 35.0)); m = m.map((v, j) => lerp([1, 0, 0, 1, 0, 0][j], v, q)); HEADS.mistral = HEADS.mistral;
      }
      ctx.save(); ctx.globalAlpha = a; ctx.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]); ctx.drawImage(texSrc, 0, 0);
      ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 2 / Math.hypot(m[0], m[1]); ctx.strokeRect(0, 0, W, H); ctx.restore();
      const hd = PANEL_HEAD[nm] || [W / 2, H / 2]; heads.push(apply(m, hd[0], hd[1]));
      const lb = apply(m, 0, H);
      ctx.save(); ctx.globalAlpha = a * (1 - k); ctx.font = `400 14px ${F.MONO}`; ctx.fillStyle = 'rgba(235,240,248,0.75)';
      ctx.fillText(['session 1 · codex · stranded', 'session 2 · claude · copied', 'session 3 · mistral · wrong base'][i], lb[0], lb[1] + 26); ctx.restore();
    });
    // broken continuity: dashed links between sessions, with gaps
    if (t < 41.0) {
      ctx.save(); ctx.globalAlpha = panA * ss(35.5, 36.5, t); ctx.strokeStyle = 'rgba(235,240,248,0.45)'; ctx.setLineDash([2, 9]); ctx.lineWidth = 1;
      for (let i = 0; i < 2; i++) { const a = heads[i], b = heads[i + 1], m1 = [lerp(a[0], b[0], 0.42), lerp(a[1], b[1], 0.42)], m2 = [lerp(a[0], b[0], 0.58), lerp(a[1], b[1], 0.58)]; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(m1[0], m1[1]); ctx.moveTo(m2[0], m2[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
      ctx.restore();
    }
  }
  r.flush(true);
  if (inTopo) bloom(ctx, 0.75 * topoA + 0.1, 12);
  // ---- rupture: image-space tears that follow the resident
  let resXY: [number, number] | null = null;
  if (t >= 38.9 && t < 47.0 && heads.length === 3) {
    resXY = resRupture(t, heads);
    const faults: Fault[] = [];
    const a1 = Math.atan2(600 - 1060, 820 + 90);
    if (t < 44.2) faults.push({ x: resXY[0], y: resXY[1], a: a1, shift: 70 * ss(39.1, 40.3, t) * (1 - 0.8 * ss(41.8, 43.5, t)), gap: 4, wob: 6 });
    if (t >= 40.8 && t < 41.9) { const j = Math.floor(prog(t, 40.8, 41.8) * 6), rr = rng(300 + j), fk = 1 - prog(t, 40.8 + j / 6, 40.8 + (j + 1) / 6); faults.push({ x: resXY[0], y: resXY[1], a: rr() * Math.PI, shift: 60 + 180 * fk * rr(), gap: 6 }); faults.push({ x: rr() * W, y: rr() * H, a: rr() * Math.PI, shift: 90 * fk, gap: 3 }); }
    if (t >= 44.2) { const ph = ((t - 44.2) / 0.6) % 1; faults.push({ x: resXY[0], y: resXY[1], a: t * 0.8, shift: 40 * (1 - ph) * (1 - ss(46, 47, t)), gap: 2 }); }
    faults.forEach((f) => tear(ctx, f, t, 1));
    // wrong reconnections, then the one true thread
    if (t > 41.0 && t < 42.6) {
      const rr = rng(Math.floor(t * 10)); const pts: [number, number][] = [...heads];
      for (let q = 0; q < 8; q++) pts.push([rr() * W, rr() * H]);
      ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.lineWidth = 1;
      for (let q = 0; q < 7; q++) { const a = pts[Math.floor(rr() * pts.length)], b = pts[Math.floor(rr() * pts.length)]; ctx.strokeStyle = `rgba(${spec(rr() * 360)},${0.5 * (1 - prog(t, 41.9, 42.6))})`; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
      ctx.restore();
    }
    if (t > 41.8) {
      const k = eio(prog(t, 41.8, 44.0)) * 2;
      ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = 1 - ss(45.6, 46.8, t);
      for (const [w, a] of [[8, 0.12], [2.4, 1]] as [number, number][]) {
        ctx.strokeStyle = `rgba(255,255,255,${a})`; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(heads[0][0], heads[0][1]);
        for (let j = 0; j < 2; j++) { if (k <= j) break; const f = Math.min(1, k - j); ctx.lineTo(lerp(heads[j][0], heads[j + 1][0], f), lerp(heads[j][1], heads[j + 1][1], f)); }
        ctx.stroke();
      }
      ctx.restore();
    }
  }
  if (inTopo && r.faults.length) fracture(ctx, r.faults[0], t, 0.35 * topoA, 520);
  // resident: outside every projection
  let rx = W / 2, ry = H / 2, ra = 0;
  if (resXY) { rx = resXY[0]; ry = resXY[1]; ra = ss(38.9, 39.3, t); }
  if (t >= 47.0) { const P0 = r.P(rp); rx = P0[0]; ry = P0[1]; ra = 1; }
  if (t >= 47.0 && t < 47.6) { const k = eio(prog(t, 47.0, 47.6)); const q = resRupture(46.9, heads.length === 3 ? heads : [[W / 2, H / 2], [W / 2, H / 2], [W / 2, H / 2]]); rx = lerp(q[0], rx, k); ry = lerp(q[1], ry, k); }
  if (col > 0) { rx = lerp(rx, W / 2, col); ry = lerp(ry, H / 2, col); }
  resident(ctx, rx, ry, ra, t, 1 + 0.6 * Math.sin(t * 3));
  if (t > 38.9 && t < 41.2) { ctx.save(); ctx.globalAlpha = win(t, 39.3, 41.0, 0.3, 0.3); ctx.font = `400 14px ${F.MONO}`; ctx.fillStyle = '#fff'; (ctx as any).letterSpacing = '4px'; ctx.fillText('RESIDENT', rx + 46, ry + 5); ctx.restore(); }
  // reading tag
  if (t >= 47.8 && t < 75.4) {
    const a = Math.min(1, ...SW.map((x) => 1 - win(t, x - 0.5, x + 0.5, 0.1, 0.1))) * win(t, 47.8, 75.4, 0.4, 0.4);
    ctx.save(); ctx.globalAlpha = a; ctx.fillStyle = 'rgba(235,240,248,0.9)'; ctx.font = `600 15px ${F.SANS}`; (ctx as any).letterSpacing = '10px'; ctx.fillText(READ_NAMES[ri], 120, 116);
    ctx.font = `400 12px ${F.MONO}`; (ctx as any).letterSpacing = '2px'; ctx.fillStyle = 'rgba(235,240,248,0.5)'; ctx.fillText(`0${ri + 1} / 04 · same topology`, 120, 142); ctx.restore();
  }
}

// =============== MANIFESTO ===============
function typeLine(ctx: CanvasRenderingContext2D, parts: { s: string; font: string; col: string }[], x: number, y: number, align: 'left' | 'center' = 'left') {
  let w = 0; parts.forEach((p) => { ctx.font = p.font; w += ctx.measureText(p.s).width; });
  let cx = align === 'center' ? x - w / 2 : x;
  parts.forEach((p) => { ctx.font = p.font; ctx.fillStyle = p.col; ctx.fillText(p.s, cx, y); cx += ctx.measureText(p.s).width; });
  return [align === 'center' ? x - w / 2 : x, w];
}
export function manifesto(ctx: CanvasRenderingContext2D, t: number) {
  ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1; ctx.fillStyle = '#010102'; ctx.fillRect(0, 0, W, H);
  ctx.save(); (ctx as any).letterSpacing = '-5px'; ctx.textBaseline = 'alphabetic';
  const BIG = (n: number) => `800 ${n}px ${F.SANS}`;
  let res: [number, number] = [W / 2, H / 2], ra = 1;
  if (t < 79.4) res = [W / 2, H / 2];
  // line 1
  const l1 = win(t, 79.4, 83.5, 0.05, 0.35);
  if (l1 > 0) {
    const styles = [{ font: `500 150px ${F.MONO}`, col: `rgb(${C.blue})` }, { font: `italic 400 176px ${F.SERIF}`, col: `rgb(${C.orange})` }, { font: `400 118px ${F.PIX}`, col: `rgb(${C.amber})` }, { font: `300 150px ${F.SANS}`, col: `rgb(${spec(t * 200 % 360)})` }];
    const cyc = t > 80.0 && t < 82.0 ? styles[Math.floor(t * 10) % 4] : { font: BIG(150), col: '#fff' };
    ctx.globalAlpha = l1;
    typeLine(ctx, [{ s: 'THE ', font: BIG(150), col: '#f2f4f8' }, { s: 'MODEL', font: cyc.font, col: cyc.col }], 330, 500);
    typeLine(ctx, [{ s: 'IS REPLACEABLE.', font: BIG(150), col: '#f2f4f8' }], 330, 650);
    res = [lerp(W / 2, 250, eio(prog(t, 79.4, 79.9))), lerp(H / 2, 450, eio(prog(t, 79.4, 79.9)))];
  }
  // line 2 — the work, perfectly still, underlined by its own lineage
  const l2 = win(t, 83.8, 87.6, 0.05, 0.35);
  if (l2 > 0) {
    ctx.globalAlpha = l2;
    typeLine(ctx, [{ s: 'THE WORK', font: BIG(150), col: '#fff' }], 330, 500);
    typeLine(ctx, [{ s: 'IS YOURS.', font: BIG(150), col: '#fff' }], 330, 650);
    const k = eio(prog(t, 84.1, 85.6)), x0 = 330, x1 = 1600, y = 730;
    const cols = [C.blue, '232,140,105', C.amber, C.violet, '255,255,255'];
    ctx.globalCompositeOperation = 'lighter'; ctx.lineWidth = 2.4;
    for (let i = 0; i < 5; i++) { const a = x0 + (x1 - x0) * i / 5, b = Math.min(x0 + (x1 - x0) * (i + 1) / 5, x0 + (x1 - x0) * k); if (b <= a) break; ctx.strokeStyle = `rgba(${cols[i]},1)`; ctx.beginPath(); ctx.moveTo(a, y); ctx.lineTo(b, y); ctx.stroke(); }
    for (let q = 0; q < 14; q++) { const x = x0 + 40 + q * 90; if (x > x0 + (x1 - x0) * k) break; ctx.fillStyle = `rgb(${cols[Math.min(4, Math.floor(((x - x0) / (x1 - x0)) * 5))]})`; ctx.fillRect(x - 5, y - 5, 10, 10); }
    ctx.globalCompositeOperation = 'source-over';
    res = [x0 + (x1 - x0) * k, y];
  }
  // line 3 — the fracture stays
  const l3 = win(t, 88.0, 91.3, 0.05, 0.35);
  if (l3 > 0) {
    ctx.globalAlpha = l3; (ctx as any).letterSpacing = '-7px';
    typeLine(ctx, [{ s: 'OWN YOUR AGENTS.', font: BIG(196), col: '#fff' }], W / 2, 610, 'center');
    res = [W / 2, 790];
  }
  // wordmark
  const l4 = win(t, 91.6, 96.0, 0.5, 1.0);
  if (l4 > 0) {
    ctx.globalAlpha = l4; (ctx as any).letterSpacing = '-3px';
    const [x] = typeLine(ctx, [{ s: 'brnrd', font: `700 140px ${F.SANS}`, col: '#fff' }], W / 2 + 40, 590, 'center');
    (ctx as any).letterSpacing = '6px'; ctx.font = `400 18px ${F.MONO}`; ctx.fillStyle = 'rgba(235,240,248,0.55)'; ctx.textAlign = 'center'; ctx.fillText('brnrd.dev', W / 2 + 40, 660); ctx.textAlign = 'left';
    res = [x - 70, 548]; ra = l4;
  }
  ctx.restore();
  // tears: every line enters misregistered and heals — except the last, which keeps its fault
  const tears: Fault[] = [];
  for (const [a, b] of [[79.4, 83.5], [83.8, 87.6]]) {
    const inn = 1 - eout(prog(t, a, a + 0.55)), out = ein(prog(t, b - 0.35, b));
    if (t >= a && t < b) tears.push({ x: W / 2, y: 575, a: -0.05, shift: 320 * inn + 260 * out, gap: 10 * inn });
  }
  if (t >= 88.0 && t < 91.3) tears.push({ x: W / 2, y: 548, a: -0.04, shift: 14 + 300 * (1 - eout(prog(t, 88.0, 88.6))) + 300 * ein(prog(t, 90.95, 91.3)) + 4 * Math.sin(t * 9), gap: 3, wob: 2 });
  tears.forEach((f) => tear(ctx, f, t, 1));
  const fade = 1 - ss(95.2, 96, t);
  const between = t > 83.5 && t < 83.8 || t > 87.6 && t < 88.0 || t > 91.3 && t < 91.6;
  resident(ctx, res[0], res[1], ra * fade * (between ? 0.6 : 1), t, 0.8);
}

// =============== captions + tags (screen space) ===============
const CAPS: [number, number, string, string][] = [
  [2.4, 4.7, '01', 'one piece of work'],
  [7.7, 10.3, '02', 'follow-up: retry on 409'],
  [11.2, 13.5, '03', 'usage limit. the work is stranded.'],
  [14.3, 17.2, '04', 'context, carried by hand'],
  [20.6, 23.9, '05', 'rebuilt from what survived'],
  [26.8, 29.4, '06', 'fresh session. no memory.'],
  [31.6, 33.8, '07', 'release cut from the wrong lineage'],
  [35.8, 38.6, '08', 'three sessions. one work. no continuity.'],
  [72.3, 75.3, '09', 'one lineage. any model.'],
];
function captions(ctx: CanvasRenderingContext2D, t: number) {
  const light = t >= 17.9 && t < 25.2, pix = t >= 25.9 && t < 34;
  const col = light ? '35,28,22' : '235,240,248';
  ctx.save(); ctx.globalCompositeOperation = 'source-over';
  for (const [a, b, idx, txt] of CAPS) {
    const al = win(t, a, b, 0.2, 0.4); if (al <= 0) continue;
    const rv = eout(prog(t, a, a + 0.45));
    ctx.globalAlpha = al;
    ctx.font = `400 13px ${F.MONO}`; ctx.fillStyle = `rgba(${col},0.5)`; (ctx as any).letterSpacing = '2px'; ctx.fillText(idx, 120, 958);
    ctx.fillStyle = `rgba(${col},0.45)`; ctx.fillRect(120, 972, 44 * rv, 1);
    ctx.save(); ctx.beginPath(); ctx.rect(160, 900, 1400 * rv, 100); ctx.clip();
    ctx.font = pix ? `400 26px ${F.PIX}` : `500 32px ${F.SANS}`; (ctx as any).letterSpacing = pix ? '1px' : '-0.5px'; ctx.fillStyle = `rgba(${col},0.95)`; ctx.fillText(txt, 164, 962);
    ctx.restore();
  }
  // world tags
  const tag = (a: number, b: number, draw: () => void) => { const al = win(t, a, b, 0.3, 0.3); if (al > 0) { ctx.globalAlpha = al; draw(); } };
  tag(4.8, 13.6, () => { ctx.font = `500 15px ${F.MONO}`; (ctx as any).letterSpacing = '9px'; ctx.fillStyle = `rgba(${C.ice},0.85)`; ctx.fillText('CODEX', 120, 116); ctx.font = `400 12px ${F.MONO}`; (ctx as any).letterSpacing = '2px'; ctx.fillStyle = `rgba(${C.ice},0.45)`; ctx.fillText('session 1', 120, 140); });
  tag(18.6, 25.0, () => { ctx.font = `italic 400 36px ${F.SERIF}`; (ctx as any).letterSpacing = '0px'; ctx.fillStyle = `rgb(${C.orange})`; ctx.fillText('Claude', 120, 122); ctx.font = `400 12px ${F.MONO}`; (ctx as any).letterSpacing = '2px'; ctx.fillStyle = 'rgba(35,28,22,0.5)'; ctx.fillText('session 2', 120, 146); });
  tag(26.4, 33.8, () => { ctx.font = `400 22px ${F.PIX}`; (ctx as any).letterSpacing = '2px'; ctx.fillStyle = 'rgb(255,150,40)'; ctx.fillText('MISTRAL', 120, 118); ctx.font = `400 12px ${F.PIX}`; ctx.fillStyle = 'rgba(255,190,80,0.6)'; ctx.fillText('session 3', 120, 142); });
  ctx.restore();
}

// =============== master ===============
export function mbSamples(t: number) {
  if (t >= 40.75 && t < 41.9) return 4;
  for (const x of SW) if (Math.abs(t - x) < 0.55) return 4;
  if (t >= LIMIT - 0.25 && t < LIMIT + 0.1) return 3;
  if (t >= REL - 0.25 && t < REL + 0.05) return 3;
  if (t >= 75.6 && t < 78) return 3;
  return 1;
}
export function drawFrame(ctx: CanvasRenderingContext2D, t: number) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (t < 17.95) { codexWorld(ctx, t); carry(ctx, t); }
  else if (t < 24.9) claudeWorld(ctx, t);
  else if (t < 25.9) {
    // claude burns down into bitmap
    const c = off('csrc'); claudeWorld(ctx2(c), t);
    const k = prog(t, 24.9, 25.9), blk = Math.max(1, Math.round(lerp(1, 7, ein(k))));
    if (blk <= 1) { ctx.globalCompositeOperation = 'copy'; ctx.drawImage(c, 0, 0); ctx.globalCompositeOperation = 'source-over'; }
    else bitmapify(c, ctx, blk, PAL, 0.25, ss(0.2, 0.9, k));
    if (k > 0.55) { const m = off('mx'); mistralWorld(ctx2(m), t); ctx.save(); ctx.globalAlpha = ss(0.55, 1, k); ctx.drawImage(m, 0, 0); ctx.restore(); }
  } else if (t < 33.9) mistralWorld(ctx, t);
  else if (t < 79.0) topoWorld(ctx, t);
  else manifesto(ctx, t);
  if (t < 0.25 || t > 95.9) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H); }
  captions(ctx, t);
}
export function finish(ctx: CanvasRenderingContext2D, t: number) {
  const light = t >= 17.9 && t < 25.2;
  grain(ctx, t, light ? 0.05 : 0.08, !light);
  vignette(ctx, light ? 0.25 : 0.55, light ? '60,40,20' : '0,0,0');
}
export const DURATION = 96;
