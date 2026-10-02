'use strict';
// brnrd — brand film. One continuous system: 16 orbital strands that never
// disappear, only reconfigure, with plasma travelling through them.
// Deterministic: everything is a pure function of t, so render.js can step it.

const SS = 2;                 // supersample factor
const W = 1080;               // logical / output size
const PX = W * SS;
const DUR = 14.0;
const TAU = Math.PI * 2;

const out = document.getElementById('c');
out.width = W; out.height = W;
const octx = out.getContext('2d');

function mk(w, h) { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; }
const M = mk(PX, PX), ctx = M.getContext('2d');
const E = mk(PX, PX), ex = E.getContext('2d');
const B1 = mk(PX / 4, PX / 4), b1 = B1.getContext('2d');
const B2 = mk(PX / 8, PX / 8), b2 = B2.getContext('2d');

// ───────────────────────── math ─────────────────────────
const clamp = (x, a = 0, b = 1) => x < a ? a : x > b ? b : x;
const lerp = (a, b, t) => a + (b - a) * t;
const ramp = (t, a, b) => clamp((t - a) / (b - a));
const eio = x => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const eoc = x => 1 - Math.pow(1 - x, 3);
const eox = x => x >= 1 ? 1 : 1 - Math.pow(2, -10 * x);
const eic = x => x * x * x;
const frac = x => x - Math.floor(x);
const h1 = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const wrap = (x, r) => ((x + r) % (2 * r) + 2 * r) % (2 * r) - r;

function kf(keys, t, ease = eio) {
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    if (t <= keys[i][0]) {
      const [t0, v0] = keys[i - 1], [t1, v1] = keys[i];
      return lerp(v0, v1, ease((t - t0) / (t1 - t0)));
    }
  }
  return keys[keys.length - 1][1];
}
const lin = x => x;

// ───────────────────────── 3D ─────────────────────────
const F = 1500;
function rotX(p, a) { const c = Math.cos(a), s = Math.sin(a); return [p[0], p[1] * c + p[2] * s, -p[1] * s + p[2] * c]; }
function rotY(p, a) { const c = Math.cos(a), s = Math.sin(a); return [p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c]; }
function rotZ(p, a) { const c = Math.cos(a), s = Math.sin(a); return [p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2]]; }
function proj(p) { const k = F / (F + p[2]); return [p[0] * k, p[1] * k, p[2], k]; }

// ───────────────────────── palette ─────────────────────────
const CREAM = '#efe5d6';
const AMBER = '#f5a445';
const BG = '#040302';

// ───────────────────────── timeline ─────────────────────────
// Scene configurations of the strand system and the windows in which the
// strands reconfigure from one to the next (staggered per strand).
const TR = [[2.45, 3.55], [4.65, 5.75], [7.35, 8.6], [10.1, 11.2], [11.38, 12.2]];
// how much of each transition window a single strand spends moving (1 = all in unison)
const TR_SPAN = [.62, .62, .93, .62, .66];
const TR_BEND = [1, 1, .12, 1, .55];
const ZOOM = [[0, .955], [2.45, 1.03], [3.4, .99], [4.65, 1.04], [5.7, .985], [7.35, 1.03], [8.7, .935],
  [10.1, .975], [11.3, 1.015], [12.2, .985], [14, 1.012]];
const ENERGY = [[0, .12], [2.6, .15], [3.5, .8], [5, .85], [7.4, 1], [10.1, 1], [11.1, .45], [11.8, .18], [12.3, 0], [14, 0]];
const SPEED = [[0, .3], [2.4, .35], [3.5, .9], [5, 1], [7.4, 1.25], [10, 1.3], [11, .6], [12.2, .35], [14, .3]];

// ∫speed dt, tabulated so comets never jump when the tempo changes.
const GT = [];
{ let acc = 0; for (let k = 0; k <= (DUR + 2) * 240; k++) { GT.push(acc); acc += kf(SPEED, k / 240, lin) / 240; } }
function G(t) { const x = clamp(t, 0, DUR + 1) * 240; const i = Math.floor(x); return lerp(GT[i], GT[i + 1], x - i); }

// ───────────────────────── strands ─────────────────────────
const NS = 16;
const ST = [];
for (let i = 0; i < NS; i++) {
  ST.push({
    ord: h1(i * 3.17 + 1.3),
    inc: (h1(i + 11) - .5) * .8,
    om: h1(i + 23) * TAU,
    var: .6 + .4 * h1(i + 5),
    dashed: i % 3 === 1,
    ruler: i === 6 || i === 13,
    vias: [h1(i + 40), h1(i + 41), h1(i + 42)],
    stubs: [h1(i + 50), h1(i + 51)],
    comets: [0, 1, 2].map(c => ({
      ph: h1(i * 7 + c * 13 + 3),
      v: (.045 + .05 * h1(i * 5 + c * 3 + 9)) * (h1(i + c * 31) > .5 ? 1 : -1),
      th: h1(i * 11 + c * 17),
      len: .045 + .04 * h1(i * 19 + c),
    })),
  });
}

const NODES = [[-250, -150], [250, -150], [250, 150], [-250, 150]];
const NODE_NAMES = ['RESEARCH', 'BUILD', 'AUTOMATE', 'FOLLOW UP'];
function superE(th) {
  const c = Math.cos(th), s = Math.sin(th);
  return [297 * Math.sign(c) * Math.pow(Math.abs(c), .5), 178 * Math.sign(s) * Math.pow(Math.abs(s), .5)];
}

// Lockup geometry, filled in by layoutEnd() once fonts are ready.
const L = { DX: 0, DY: -60, DH: 40 };

// Scene 0 — an orrery around the opening line; contracts on "too small".
function shape0(i, u, t) {
  const s = ST[i];
  const con = 1 - .12 * eio(ramp(t, 1.75, 2.35)) + .025 * Math.sin(Math.PI * ramp(t, 1.7, 1.9));
  const r = (330 + i * 19) * con * (1 + .006 * Math.sin(t * 1.3 + i));
  const th = u * TAU + i * .7 + t * (.05 + .02 * (i % 3));
  let p = [r * Math.cos(th), r * Math.sin(th), 0];
  p = rotX(p, s.inc);
  return rotZ(p, s.om + t * .03);
}
// Scene 1 — two orbit systems, interlinked: you and a colleague.
function shape1(i, u, t) {
  const g = i < 8 ? -1 : 1, j = i % 8;
  const R = 230 + j * 27;
  const th = u * TAU + j * .5 + g * t * .12;
  let p = [R * Math.cos(th), R * Math.sin(th), 0];
  p = rotX(p, .18 + j * .035 * g);
  p = rotY(p, -g * (.78 + j * .025) + Math.sin(t * .4) * .04);
  return [p[0] + g * 190, p[1], p[2]];
}
// Scene 2/3 — four functions on one great orbit, spokes to a resident core.
function shape2(i, u, t) {
  if (i < 4) {
    const [nx, ny] = NODES[i]; const th = u * TAU + t * 1.6;
    return [nx + 40 * Math.cos(th), ny + 40 * Math.sin(th), 0];
  }
  if (i < 8) {
    const k = i - 4, [nx, ny] = NODES[k], r = 66 + k * 3, th = u * TAU + t * .9;
    let p = [r * Math.cos(th), r * Math.sin(th), 0];
    p = rotX(p, 1.15); p = rotZ(p, k * .9 + t * .5);
    return [p[0] + nx, p[1] + ny, p[2]];
  }
  if (i < 12) {
    const k = i - 8; const th = -.75 * Math.PI + k * Math.PI / 2 + u * Math.PI / 2;
    const [x, y] = superE(th);
    return [x, y, -30 * Math.sin(Math.PI * u)];
  }
  const k = i - 12, [nx, ny] = NODES[k];
  const cx = nx * .1, cy = ny * .9;
  const b = 2 * u * (1 - u), c = u * u;
  return [b * cx + c * nx, b * cy + c * ny, -50 * Math.sin(Math.PI * u)];
}
// Scene 4 — calm, Keplerian orbits around the resident.
function shape4(i, u, t) {
  const R = 70 + i * 30;
  const th = u * TAU + t * .9 / Math.sqrt(R / 70) + i * 1.3;
  return [R * Math.cos(th), R * Math.sin(th), 0];
}
// Scene 5 — every strand condenses into the lockup's divider.
function shape5(i, u, t) {
  const y = -L.DH + 2 * L.DH * (1 - Math.abs(2 * u - 1));
  return [L.DX, L.DY + y, 0];
}
const SH = [shape0, shape1, shape2, shape2, shape4, shape5];
const ID = { pitch: 0, yaw: 0, spin: 0, sc: 1, ox: 0, oy: 0 };
const PRM = [
  () => ID,
  () => ID,
  t => ({ pitch: .2 + .03 * Math.sin(t * .6), yaw: .07 * Math.sin(t * .45), spin: 0, sc: 1, ox: 0, oy: 90 }),
  t => ({ pitch: 1.08, yaw: 0, spin: Math.max(0, t - 7.35) * .16, sc: 1.28, ox: 0, oy: 250 }),
  t => ({ pitch: 1.16, yaw: 0, spin: 0, sc: 1, ox: 0, oy: 245 }),
  () => ID,
];
function xf(p, q) {
  let v = rotZ(p, q.spin);
  v = [v[0] * q.sc, v[1] * q.sc, v[2] * q.sc];
  v = rotX(v, q.pitch); v = rotY(v, q.yaw);
  return [v[0] + q.ox, v[1] + q.oy, v[2]];
}
function lerpP(a, b, w) { const o = {}; for (const k in a) o[k] = lerp(a[k], b[k], w); return o; }

function stage(ord, t) {
  for (let k = TR.length - 1; k >= 0; k--) {
    const [a, b] = TR[k];
    if (t >= a) {
      const span = b - a, d = span * TR_SPAN[k], st = a + (span - d) * ord;
      const w = eio(clamp((t - st) / d));
      return w < 1 ? [k, w] : [k + 1, 0];
    }
  }
  return [0, 0];
}
function windowOf(k, i, t) {
  if (k === 0) {
    const o = ST[i].ord, s = o * .4, f = eio(ramp(t, .05 + o * .7, 1.3 + o * .7));
    return [s, s + f];
  }
  if (k === 2 || k === 3) { if (i >= 8 && i < 12) return [.1, .9]; if (i >= 12) return [.14, .86]; }
  return [0, 1];
}
function frameOf(i, t) {
  const [k, w] = stage(ST[i].ord, t);
  const qa = PRM[k](t);
  const fr = { i, t, k, w, qa, q: qa, win: windowOf(k, i, t) };
  if (w > 0) {
    fr.qb = PRM[k + 1](t); fr.q = lerpP(qa, fr.qb, w);
    const wb = windowOf(k + 1, i, t);
    fr.win = [lerp(fr.win[0], wb[0], w), lerp(fr.win[1], wb[1], w)];
  }
  fr.end = k === 5 ? 1 : k === 4 ? w : 0;
  return fr;
}
function pt(fr, u) {
  const { i, t, k, w } = fr;
  let p = SH[k](i, u, t);
  if (w > 0) {
    const pb = SH[k + 1](i, u, t);
    const s = Math.sin(Math.PI * w) * TR_BEND[k], a = u * TAU;
    p = [lerp(p[0], pb[0], w) + s * 45 * Math.sin(a * 2 + i * 1.3 + k),
      lerp(p[1], pb[1], w) + s * 45 * Math.cos(a * 3 + i * .7 + k * 2),
      lerp(p[2], pb[2], w) + s * 120 * Math.sin(a + i)];
  }
  return proj(xf(p, fr.q));
}
function inWin(u, win) { const a = win[0]; const v = a + frac(u - a); return v <= win[1]; }

// ───────────────────────── sprites ─────────────────────────
function sprite(r, stops) {
  const c = mk(r * 2, r * 2), g = c.getContext('2d');
  const gr = g.createRadialGradient(r, r, 0, r, r, r);
  stops.forEach(([o, col]) => gr.addColorStop(o, col));
  g.fillStyle = gr; g.fillRect(0, 0, r * 2, r * 2); return c;
}
const GLOW = sprite(64, [[0, 'rgba(255,170,80,1)'], [.25, 'rgba(255,130,40,.45)'], [.6, 'rgba(200,70,15,.12)'], [1, 'rgba(120,30,0,0)']]);
const HOT = sprite(32, [[0, 'rgba(255,250,235,1)'], [.35, 'rgba(255,215,150,.8)'], [1, 'rgba(255,160,60,0)']]);
function blit(c, img, x, y, size, a) {
  if (a <= .002) return;
  c.globalAlpha = Math.min(1, a); c.drawImage(img, x - size / 2, y - size / 2, size, size);
}

// ───────────────────────── text ─────────────────────────
const scratch = mk(8, 8).getContext('2d');
const LINES = [];
class Line {
  constructor(o) {
    Object.assign(this, { fam: 'IT', weight: 700, size: 60, ls: 0, color: CREAM, alpha: 1, x: 0, y: 0,
      tIn: 0, dIn: .85, st: .022, tOut: 99, dOut: .6, out: 'dissolve', spread: 2.2, glow: 0, sheen: false, grad: null }, o);
    this.build(); LINES.push(this);
  }
  font(s = SS) { return `${this.weight} ${this.size * s}px ${this.fam}`; }
  build() {
    const c = scratch; c.font = this.font(); c.letterSpacing = `${this.ls * SS}px`;
    const text = this.text, n = text.length, pad = Math.ceil(this.size * .35);
    this.pad = pad;
    this.width = c.measureText(text).width / SS - this.ls;
    this.h = Math.ceil(this.size * 1.7);
    this.by = Math.ceil(pad + this.size * 1.0);
    this.chars = [];
    const parts = [];
    for (let k = 0; k < n; k++) {
      const x = c.measureText(text.slice(0, k)).width / SS;
      const adv = c.measureText(text.slice(0, k + 1)).width / SS - x;
      const ch = text[k];
      if (ch === ' ') { this.chars.push({ x, adv, img: null }); continue; }
      const cw = Math.ceil(adv + pad * 2), cv = mk(cw * SS, this.h * SS), g = cv.getContext('2d');
      g.font = this.font(); g.letterSpacing = '0px';
      if (this.grad) {
        const gr = g.createLinearGradient((-x + pad) * SS, 0, (this.width - x + pad) * SS, 0);
        this.grad.forEach(([o, col]) => gr.addColorStop(o, col));
        g.fillStyle = gr;
      } else g.fillStyle = this.color;
      g.fillText(ch, pad * SS, this.by * SS);
      this.chars.push({ x, adv, img: cv });
      parts.push([k, x, cv]);
    }
    // particle samples for the dissolve
    this.parts = [];
    for (const [k, x, cv] of parts) {
      const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
      const step = 3;
      for (let yy = 0; yy < cv.height; yy += step) for (let xx = 0; xx < cv.width; xx += step) {
        const a = d[(yy * cv.width + xx) * 4 + 3];
        const id = this.parts.length + k * 977;
        if (a > 140 && h1(id * .37 + xx * .011 + yy * .007) > .42) {
          this.parts.push({ k, lx: x - pad + xx / SS, ly: yy / SS - this.by, h: [h1(id + .1), h1(id + .2), h1(id + .3), h1(id + .4), h1(id + .5)] });
        }
      }
    }
    this.lx0 = 20;
    this.T = mk(Math.ceil((this.width + 40 + pad * 2) * SS), this.h * SS);
    this.tx = this.T.getContext('2d');
  }
  visible(t) { return t >= this.tIn - .01 && t <= this.tOut + (this.out === 'dissolve' ? 1.6 : this.dOut + .05); }
  // Returns the composite job for this frame (drawn later, after bloom).
  render(t, ax = this.x, ay = this.y, extraA = 1) {
    if (!this.visible(t) || extraA <= .002) return null;
    const n = this.chars.length, tc = this.tx, T = this.T;
    tc.setTransform(1, 0, 0, 1, 0, 0); tc.clearRect(0, 0, T.width, T.height);
    tc.globalCompositeOperation = 'source-over';
    let lineA = 1, blur = 0, scale = 1, dy = 0;
    const inDur = this.dIn + n * this.st;
    blur += (1 - eoc(clamp((t - this.tIn) / (inDur * .8)))) * 7;
    if (this.out === 'fade' || this.out === 'recede') {
      const po = eio(clamp((t - this.tOut) / this.dOut));
      lineA *= 1 - po; blur += po * (this.out === 'recede' ? 12 : 5);
      if (this.out === 'recede') scale = 1 - .13 * po; else dy = -8 * po;
    }
    for (let k = 0; k < n; k++) {
      const c = this.chars[k]; if (!c.img) continue;
      const pc = eox(clamp((t - this.tIn - k * this.st) / this.dIn));
      let a = clamp((t - this.tIn - k * this.st) / (this.dIn * .45));
      if (this.out === 'dissolve') a *= 1 - eic(clamp((t - this.tOut - (c.x / this.width) * .3) / .32));
      if (a <= .002) continue;
      const ox = (k - (n - 1) / 2) * (1 - pc) * this.spread;
      const oy = (1 - pc) * this.size * .32;
      tc.globalAlpha = a;
      tc.drawImage(c.img, (this.lx0 + c.x - this.pad + ox) * SS, oy * SS);
    }
    tc.globalAlpha = 1;
    if (this.sheen) {
      tc.globalCompositeOperation = 'source-atop';
      const sx = (this.lx0 + (frac((t - this.tIn) * .32) * 1.8 - .4) * this.width) * SS;
      const g = tc.createLinearGradient(sx - 180 * SS, 0, sx + 180 * SS, 0);
      g.addColorStop(0, 'rgba(255,240,210,0)'); g.addColorStop(.5, 'rgba(255,244,222,.55)'); g.addColorStop(1, 'rgba(255,240,210,0)');
      tc.fillStyle = g; tc.fillRect(0, 0, T.width, T.height);
      tc.globalCompositeOperation = 'source-over';
    }
    const left = ax - this.width / 2 - this.lx0, top = ay - this.by + dy;
    // dissolve particles go into the energy layer so they bloom with the system
    if (this.out === 'dissolve' && t > this.tOut) this.dissolve(t, ax, ay, extraA);
    if (this.glow > 0) {
      ex.globalAlpha = this.glow * lineA * extraA;
      ex.drawImage(T, left, top, T.width / SS, T.height / SS);
      ex.globalAlpha = 1;
    }
    return { T, left, top, a: lineA * extraA * this.alpha, blur, scale, cx: ax, cy: ay - this.size * .35 };
  }
  dissolve(t, ax, ay, extraA) {
    const x0 = ax - this.width / 2;
    let lastC = -1;
    for (const p of this.parts) {
      const xr = (this.chars[p.k].x) / this.width;
      const q = clamp((t - this.tOut - xr * .3 - p.h[0] * .14) / (1.05 + p.h[1] * .45));
      if (q <= 0 || q >= 1) continue;
      const ox = x0 + p.lx, oy = ay + p.ly;
      const r = Math.hypot(ox, oy) + 1, a0 = Math.atan2(oy, ox);
      const m = Math.pow(q, 1.5);
      const r2 = r + m * (60 + 130 * p.h[2]);
      const a2 = a0 + m * (.16 + .3 * p.h[3]);
      const x = r2 * Math.cos(a2) + m * (p.h[4] - .5) * 70;
      const y = r2 * Math.sin(a2) + m * (p.h[0] - .5) * 60 - m * 36 * p.h[1];
      const al = Math.pow(1 - q, 1.4) * clamp(q * 14) * extraA * this.alpha * .9;
      const ci = q < .18 ? 0 : q < .5 ? 1 : 2;
      if (ci !== lastC) { ex.fillStyle = ci === 0 ? '#fff1dc' : ci === 1 ? '#ffc070' : '#f07a24'; lastC = ci; }
      ex.globalAlpha = al;
      const s = 1.25 * (1 - q * .45);
      ex.fillRect(x - s / 2, y - s / 2, s, s);
    }
    ex.globalAlpha = 1;
  }
}

function drawJob(j) {
  if (!j || j.a <= .002) return;
  ctx.save();
  if (j.scale !== 1) { ctx.translate(j.cx, j.cy); ctx.scale(j.scale, j.scale); ctx.translate(-j.cx, -j.cy); }
  ctx.globalAlpha = Math.min(1, j.a);
  if (j.blur > .15) ctx.filter = `blur(${(j.blur * SS).toFixed(2)}px)`;
  ctx.drawImage(j.T, j.left, j.top, j.T.width / SS, j.T.height / SS);
  ctx.restore();
}

// ───────────────────────── HugiMuni mark (canonical stroke geometry) ─────────────────────────
const HM_H = [[145, 156, 145, 356, 40], [353, 156, 353, 356, 40], [132, 276, 380, 276, 28]];
const HM_M = [[159, 156, 159, 356, 40], [367, 156, 367, 356, 40], [132, 156, 276, 356, 28], [380, 156, 236, 356, 28]];
function strokes(g, list, col, s, ox, oy) {
  g.strokeStyle = col; g.lineCap = 'round';
  for (const [x1, y1, x2, y2, w] of list) {
    g.lineWidth = w * s; g.beginPath(); g.moveTo(ox + x1 * s, oy + y1 * s); g.lineTo(ox + x2 * s, oy + y2 * s); g.stroke();
  }
}
// Flat register: amber = H only, sky = M only, cream = H ∩ M.
function drawMark(g, s, ox, oy) {
  strokes(g, HM_H, '#ff9a1f', s, ox, oy);
  strokes(g, HM_M, '#69c7df', s, ox, oy);
  const c = mk(g.canvas.width, g.canvas.height), x = c.getContext('2d');
  strokes(x, HM_M, '#f0e3cf', s, ox, oy);
  x.globalCompositeOperation = 'destination-in';
  strokes(x, HM_H, '#000', s, ox, oy);
  g.drawImage(c, 0, 0);
}

let HMG = null; // the parent-company group: mark + wordmark, pre-rendered
function buildHM() {
  const markH = 58, sM = markH / 240;              // 240 units incl. caps
  const fs = 34, sG = fs * .755 / 200;             // glyph cap height ≈ text cap height
  scratch.font = `500 ${fs * SS}px IT`; scratch.letterSpacing = '0px';
  const wUgi = scratch.measureText('ugi').width / SS, wUni = scratch.measureText('uni').width / SS;
  const gw = (380 - 132 + 28) * sG;               // glyph visual width incl. caps
  const markW = 288 * sM, gap = 20, gapG = 2;
  const w = Math.ceil(markW + gap + gw + gapG + wUgi + 4 + gw + gapG + wUni + 8), h = 80;
  const c = mk(w * SS, h * SS), g = c.getContext('2d');
  g.scale(SS, SS);
  const cy = h / 2;
  drawMark(g, sM, -112 * sM, cy - 256 * sM);
  // wordmark: H glyph + "ugi" in amber, M glyph + "uni" in sky, glyphs sit on the baseline
  const base = cy + fs * .755 / 2;
  let x = markW + gap;
  strokes(g, HM_H, '#ff9a1f', sG, x - (132 - 14) * sG, base - 356 * sG); x += gw + gapG;
  g.font = `500 ${fs}px IT`; g.fillStyle = '#ff9a1f'; g.fillText('ugi', x, base); x += wUgi + 4;
  strokes(g, HM_M, '#69c7df', sG, x - (132 - 14) * sG, base - 356 * sG); x += gw + gapG;
  g.fillStyle = '#69c7df'; g.fillText('uni', x, base);
  HMG = { c, w, h };
}

// ───────────────────────── content ─────────────────────────
let END = null;
function layoutText() {
  const cream60 = { color: CREAM, alpha: .62 };
  // Scene 1
  new Line({ text: 'I don’t want brnrd', size: 64, y: -40, tIn: .3, tOut: 2.42 });
  new Line({ text: 'to be a tool for AI geeks.', size: 64, y: 34, tIn: .55, tOut: 2.52 });
  new Line({ text: 'That’s too small.', size: 25, weight: 500, color: AMBER, y: 112, tIn: 1.68, st: .03, tOut: 2.38, out: 'fade', dOut: .45, glow: .35, ls: .3 });
  // Scene 2
  new Line({ text: 'I want it to feel like', size: 58, y: -44, tIn: 2.98, tOut: 4.68 });
  new Line({ text: 'a colleague.', size: 92, weight: 800, y: 50, tIn: 3.24, st: .035, tOut: 4.8, glow: .28, sheen: true, ls: -1.5,
    grad: [[0, '#ffd894'], [.5, '#f9b250'], [1, '#ec8a26']] });
  new Line({ text: 'someone who keeps the work moving', size: 22, weight: 400, y: 112, tIn: 3.8, st: .012, tOut: 4.6, out: 'fade', dOut: .4, ls: .4, ...cream60 });
  // Scene 3
  new Line({ text: 'while you build the business.', size: 54, y: -362, tIn: 5.02, tOut: 7.38 });
  new Line({ text: 'one resident · always reachable', fam: 'JB', size: 15, weight: 400, y: 470, tIn: 6.05, st: .012, tOut: 7.3, out: 'fade', dOut: .45, ls: 1, ...cream60 });
  // Scene 4
  new Line({ text: 'You step away.', size: 66, y: -150, tIn: 7.78, tOut: 8.55, out: 'recede', dOut: .62 });
  new Line({ text: 'It keeps working.', size: 78, weight: 800, y: -150, tIn: 9.22, st: .03, tOut: 10.25, ls: -1 });
  new Line({ text: 'fewer tabs · less babysitting · more freedom', fam: 'JB', size: 15, weight: 400, y: -84, tIn: 9.62, st: .01, tOut: 10.15, out: 'fade', dOut: .4, ls: 1, ...cream60 });
  // Scene 5
  new Line({ text: 'Build the business.', size: 64, y: -178, tIn: 10.5, tOut: 11.42 });
  new Line({ text: 'Keep the freedom.', size: 64, color: AMBER, y: -102, tIn: 10.74, tOut: 11.5, glow: .25 });
  // Node labels (positioned per frame)
  NODE_NAMES.forEach((nm, k) => new Line({ text: nm, fam: 'JB', size: 13, weight: 500, ls: 2.6, color: '#f3c58a', tIn: 5.5 + k * .16, st: .02, dIn: .6, tOut: 9.75 + k * .05, out: 'fade', dOut: .4, node: k, spread: 1 }));

  // End card
  buildHM();
  const brnrd = new Line({ text: 'brnrd', size: 132, weight: 800, ls: -4, tIn: 99, tOut: 99, out: 'fade' });
  const g = 44, total = brnrd.width + 2 * g + HMG.w;
  const left = -total / 2;
  const shift = -24;
  END = { brnrd, g, left, yB: -10 + shift };
  brnrd.x = left + brnrd.width / 2; brnrd.y = END.yB;
  L.DX = left + brnrd.width + g;
  L.DY = END.yB - 132 * .37;
  L.DH = 40;
  END.hmX = L.DX + g; END.hmY = L.DY - HMG.h / 2;
  END.url = new Line({ text: 'brnrd.dev', size: 26, weight: 500, ls: .4, y: 92 + shift, tIn: 12.42, st: .025, tOut: 99, out: 'fade' });
  END.desc = new Line({ text: 'a persistent AI coworker for small teams', size: 18, weight: 400, ls: .3, color: CREAM, alpha: .55, y: 158 + shift, tIn: 12.6, st: .01, tOut: 99, out: 'fade' });
  END.pill = { cx: 0, cy: 92 + shift - 26 * .36, w: END.url.width + 64, h: 50 };
  buildFinalPath();
}

// The resident's last journey: down the divider, around the brnrd.dev outline.
let FP = null;
function buildFinalPath() {
  const pts = [];
  const { cx, cy, w, h } = END.pill, r = h / 2;
  const top = cy - h / 2;
  for (let k = 0; k <= 20; k++) pts.push([L.DX, L.DY - L.DH + 2 * L.DH * k / 20]);
  const sx = clamp(L.DX, cx - w / 2 + r, cx + w / 2 - r);
  const a = [L.DX, L.DY + L.DH], b = [sx, top];
  for (let k = 1; k <= 24; k++) {
    const u = k / 24, v = 1 - u;
    const c1 = [L.DX, L.DY + L.DH + 18], c2 = [sx, top - 18];
    pts.push([v * v * v * a[0] + 3 * v * v * u * c1[0] + 3 * v * u * u * c2[0] + u * u * u * b[0],
      v * v * v * a[1] + 3 * v * v * u * c1[1] + 3 * v * u * u * c2[1] + u * u * u * b[1]]);
  }
  // pill perimeter clockwise from (sx, top)
  const per = [];
  const xr = cx + w / 2 - r, xl = cx - w / 2 + r;
  const seg = (x0, y0, x1, y1, n) => { for (let k = 1; k <= n; k++) per.push([lerp(x0, x1, k / n), lerp(y0, y1, k / n)]); };
  seg(sx, top, xr, top, 20);
  for (let k = 1; k <= 30; k++) { const an = -Math.PI / 2 + Math.PI * k / 30; per.push([xr + r * Math.cos(an), cy + r * Math.sin(an)]); }
  seg(xr, cy + r, xl, cy + r, 40);
  for (let k = 1; k <= 30; k++) { const an = Math.PI / 2 + Math.PI * k / 30; per.push([xl + r * Math.cos(an), cy + r * Math.sin(an)]); }
  seg(xl, top, sx, top, 20);
  pts.push(...per);
  const len = [0];
  for (let k = 1; k < pts.length; k++) len.push(len[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
  FP = { pts, len, total: len[len.length - 1] };
}
function fpAt(d) {
  const { pts, len } = FP; d = clamp(d, 0, FP.total);
  let lo = 0, hi = len.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (len[m] < d) lo = m; else hi = m; }
  const u = (d - len[lo]) / Math.max(1e-6, len[hi] - len[lo]);
  return [lerp(pts[lo][0], pts[hi][0], u), lerp(pts[lo][1], pts[hi][1], u)];
}
const FP_T0 = 12.72, FP_T1 = 13.62;

// ───────────────────────── ambient ─────────────────────────
const DUST = [];
for (let k = 0; k < 260; k++) DUST.push({
  x: (h1(k + .1) - .5) * 1400, y: (h1(k + .2) - .5) * 1400, z: -300 + h1(k + .3) * 1300,
  vx: (h1(k + .4) - .5) * 14, vy: -4 - h1(k + .5) * 10, s: .5 + h1(k + .6) * 1.1, a: .1 + Math.pow(h1(k + .7), 3) * .55,
  tw: 1 + h1(k + .8) * 3, p: h1(k + .9) * TAU,
});
function drawNebula(t, fade) {
  const blobs = [
    [-260 + 60 * Math.sin(t * .21), -180 + 40 * Math.cos(t * .17), 620, [150, 62, 14], .11],
    [300 + 50 * Math.cos(t * .19), 260 + 50 * Math.sin(t * .23), 700, [120, 45, 10], .1],
    [0, 40 * Math.sin(t * .3), 520, [255, 150, 60], .035],
  ];
  for (const [x, y, r, c, a] of blobs) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(${c},${a * fade})`); g.addColorStop(1, `rgba(${c},0)`);
    ctx.fillStyle = g; ctx.fillRect(-700, -700, 1400, 1400);
  }
}
function drawDust(t, amt) {
  ctx.fillStyle = '#ffcf98';
  for (const d of DUST) {
    const x = wrap(d.x + d.vx * t + 14 * Math.sin(t * .3 + d.p), 700);
    const y = wrap(d.y + d.vy * t + 10 * Math.cos(t * .25 + d.p), 700);
    const p = proj([x, y, d.z]);
    const a = d.a * (.6 + .4 * Math.sin(t * d.tw + d.p)) * amt * clamp(1.3 - d.z / 1000);
    if (a < .01) continue;
    const s = d.s * p[3];
    ctx.globalAlpha = a; ctx.fillRect(p[0] - s / 2, p[1] - s / 2, s, s);
    if (d.a > .4) blit(ex, GLOW, p[0], p[1], 10 * p[3], a * .25);
  }
  ctx.globalAlpha = 1;
}

// ───────────────────────── strands ─────────────────────────
function drawStrands(t, frames) {
  const sa = kf([[0, .3], [2.6, .3], [5, .36], [7.5, .3], [10, .3], [11.45, .3], [12.3, .62], [14, .62]], t);
  ctx.lineCap = 'round';
  for (const fr of frames) {
    const [a, b] = fr.win;
    if (b - a < .002) continue;
    const n = Math.max(8, Math.ceil(180 * (b - a)));
    const pts = [];
    for (let j = 0; j <= n; j++) pts.push(pt(fr, a + (b - a) * j / n));
    fr.pts = pts;
    const base = sa * ST[fr.i].var * lerp(1, 1.1, fr.end);
    const col = fr.end > 0 ? [lerp(255, 240, fr.end), lerp(182, 229, fr.end), lerp(118, 214, fr.end)] : [255, 182, 118];
    const cs = `${col[0] | 0},${col[1] | 0},${col[2] | 0}`;
    if (ST[fr.i].dashed && fr.end < 1) { ctx.setLineDash([3, 6]); ctx.lineDashOffset = -t * 22; } else ctx.setLineDash([]);
    const CH = 10;
    for (let j = 0; j < n; j += CH) {
      const m = Math.min(n, j + CH), mid = pts[(j + m) >> 1];
      const da = clamp(1.12 - mid[2] / 1000, .2, 1.25);
      ctx.strokeStyle = `rgba(${cs},${(base * da).toFixed(3)})`;
      ctx.lineWidth = .8 * mid[3];
      ctx.beginPath(); ctx.moveTo(pts[j][0], pts[j][1]);
      for (let q = j + 1; q <= m; q++) ctx.lineTo(pts[q][0], pts[q][1]);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    const deco = (1 - fr.end) * base;
    if (deco < .01) continue;
    // ruler ticks — an instrument, not a UI
    if (ST[fr.i].ruler) {
      ctx.strokeStyle = `rgba(${cs},${(deco * .9).toFixed(3)})`; ctx.lineWidth = .6;
      ctx.beginPath();
      for (let j = 1; j < n; j++) {
        const p = pts[j], q = pts[j - 1], dx = p[0] - q[0], dy = p[1] - q[1], l = Math.hypot(dx, dy) || 1;
        const len = (j % 5 === 0 ? 7 : 3.5) * p[3];
        ctx.moveTo(p[0], p[1]); ctx.lineTo(p[0] - dy / l * len, p[1] + dx / l * len);
      }
      ctx.stroke();
    }
    // vias and stubs — the circuit vocabulary
    ctx.lineWidth = .7;
    for (const u of ST[fr.i].vias) {
      if (!inWin(u, fr.win)) continue;
      const p = pt(fr, u);
      ctx.strokeStyle = `rgba(255,200,140,${(deco * 2.2).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(p[0], p[1], 2.4 * p[3], 0, TAU); ctx.stroke();
    }
    for (const u of ST[fr.i].stubs) {
      if (!inWin(u, fr.win) || !inWin(u + .003, fr.win)) continue;
      const p = pt(fr, u), q = pt(fr, u + .003);
      const dx = q[0] - p[0], dy = q[1] - p[1], l = Math.hypot(dx, dy) || 1;
      const len = 14 * p[3], ex2 = p[0] - dy / l * len, ey2 = p[1] + dx / l * len;
      ctx.strokeStyle = `rgba(255,190,130,${(deco * 1.6).toFixed(3)})`;
      ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(ex2, ey2); ctx.stroke();
      ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.arc(ex2, ey2, 1.3, 0, TAU); ctx.fill();
    }
  }
}

// ───────────────────────── plasma ─────────────────────────
function trail(fr, u, len, I, wMax = 2.4) {
  const NT = 18; let prev = null;
  for (let k = 0; k <= NT; k++) {
    const uu = u - k * len / NT;
    if (!inWin(uu, fr.win)) { prev = null; continue; }
    const p = pt(fr, uu);
    // a little turbulence — plasma, not a wire
    const wob = Math.sin(k * .9 - fr.t * 9 + fr.i) * 1.2 * (k / NT);
    p[0] += wob; p[1] -= wob * .6;
    if (prev) {
      const f = 1 - k / NT;
      ex.globalAlpha = 1;
      ex.strokeStyle = `rgba(255,${(120 + 125 * f) | 0},${(40 + 150 * f * f) | 0},${(I * f * f * .95).toFixed(3)})`;
      ex.lineWidth = (.5 + wMax * f) * p[3];
      ex.beginPath(); ex.moveTo(prev[0], prev[1]); ex.lineTo(p[0], p[1]); ex.stroke();
    }
    prev = p;
  }
  if (inWin(u, fr.win)) {
    const p = pt(fr, u);
    blit(ex, GLOW, p[0], p[1], 34 * p[3], I * .55);
    blit(ex, HOT, p[0], p[1], 7 * p[3], I);
    return p;
  }
  return null;
}
function drawEnergy(t, frames) {
  const en = kf(ENERGY, t), g = G(t);
  ex.lineCap = 'round';
  for (const fr of frames) {
    const s = ST[fr.i];
    for (let c = 0; c < 3; c++) {
      const cm = s.comets[c];
      const I = clamp(en * 3.2 - c - cm.th * .6) * (1 - fr.end);
      if (I < .02) continue;
      const u = frac(cm.ph + cm.v * g);
      const dirLen = cm.v > 0 ? cm.len : -cm.len;
      trail(fr, u, dirLen, I);
      // shed sparks: emitted along the path, drifting on their own
      ex.fillStyle = '#ffb35c';
      for (let e = 0; e < 18; e++) {
        const ti = Math.floor(t * 24) - e, te = ti / 24, age = t - te;
        if (age < 0 || age > .8) continue;
        const id = fr.i * 1000 + c * 100000 + ti;
        if (h1(id) < .45) continue;
        const ue = frac(cm.ph + cm.v * G(te));
        if (!inWin(ue, fr.win)) continue;
        const p = pt(fr, ue);
        const x = p[0] + (h1(id + .3) - .5) * 34 * age, y = p[1] + ((h1(id + .6) - .5) * 30 - 16) * age;
        ex.globalAlpha = I * Math.pow(1 - age / .8, 2) * .7 * h1(id + .9);
        ex.fillRect(x - .6, y - .6, 1.2, 1.2);
      }
      ex.globalAlpha = 1;
    }
  }
}

// ───────────────────────── network layer ─────────────────────────
const PROC = [{ p: 1.7, o: .2 }, { p: 2.1, o: .9 }, { p: 1.9, o: 1.4 }, { p: 2.4, o: .5 }];
function nodeScreen(fr, k) { return proj(xf([NODES[k][0], NODES[k][1], 0], fr.q)); }
function drawNetwork(t, frames, jobs) {
  const nw = ramp(t, 5.0, 5.85) * (1 - ramp(t, 10.1, 10.7));
  if (nw <= .001) return;
  const speed = kf([[5, 1], [7.4, 1], [8.6, 1.35], [10, 1.35]], t);
  for (let k = 0; k < 4; k++) {
    const ring = frames[k], link = frames[8 + k], spoke = frames[12 + k];
    const { p, o } = PROC[k];
    const tt = (t - o) * speed;
    const pr = frac(tt / p);
    // overlapping processes: each node runs its own cycle
    if (ring.pts) {
      const n = Math.max(2, Math.floor(ring.pts.length * pr));
      ex.strokeStyle = `rgba(255,190,110,${(nw * .85).toFixed(3)})`; ex.lineWidth = 1.5;
      ex.beginPath(); ex.moveTo(ring.pts[0][0], ring.pts[0][1]);
      for (let j = 1; j < n; j++) ex.lineTo(ring.pts[j][0], ring.pts[j][1]);
      ex.stroke();
    }
    const cyc = Math.floor(tt / p);
    // completion → hand-off along the great orbit to the next function
    for (let back = 0; back < 2; back++) {
      const tc = (cyc - back) * p, age = (tt - tc);
      if (age >= 0 && age < .85 && link.k >= 2 && link.k <= 3) {
        const u = .1 + .8 * eio(age / .85);
        trail(link, u, .16, nw * (1 - ramp(age, .7, .85)), 2.8);
      }
      // check-ins with the resident core
      const ts = tc + p * .5, ag2 = tt - ts;
      if (ag2 >= 0 && ag2 < .7 && spoke.k >= 2 && spoke.k <= 3) {
        const u = .14 + .72 * eio(ag2 / .7);
        trail(spoke, (cyc + back) % 2 ? 1 - u : u, (cyc + back) % 2 ? -.14 : .14, nw * .9, 2.2);
      }
    }
    // node body + arrival flash
    const np = nodeScreen(ring, k);
    const prev = PROC[(k + 3) % 4], tp = (t - prev.o) * speed;
    const arr = tp - (Math.floor(tp / prev.p) * prev.p + .85);
    const flash = arr >= 0 ? Math.exp(-arr * 5) : 0;
    blit(ex, GLOW, np[0], np[1], (40 + 50 * flash) * np[3], nw * (.35 + .6 * flash));
    blit(ex, HOT, np[0], np[1], 7 * np[3], nw * .9);
    const lab = LINES.find(l => l.node === k);
    jobs.push(lab.render(t, np[0], np[1] + 92 * np[3], nw));
  }
}

// ───────────────────────── the resident ─────────────────────────
function corePos(t) {
  const [k, w] = stage(.5, t);
  const loc = kk => kk === 5 ? [L.DX, L.DY - L.DH, 0] : [0, 0, 0];
  const pa = loc(k), qa = PRM[k](t);
  if (w <= 0) return proj(xf(pa, qa));
  const pb = loc(k + 1), qb = PRM[k + 1](t);
  return proj(xf([lerp(pa[0], pb[0], w), lerp(pa[1], pb[1], w), 0], lerpP(qa, qb, w)));
}
function drawCore(t) {
  const cw = ramp(t, 5.0, 5.8);
  if (cw <= 0) return;
  if (t < FP_T0) {
    const p = corePos(t);
    const sun = kf([[9.9, 0], [10.9, 1], [11.5, 1], [12.2, 0]], t);
    const pulse = .85 + .15 * Math.sin(t * 3.1);
    blit(ex, GLOW, p[0], p[1], (46 + 120 * sun) * p[3] * pulse, cw * (.55 + .2 * sun));
    blit(ex, GLOW, p[0], p[1], 16 * p[3], cw * .8);
    blit(ex, HOT, p[0], p[1], (8 + 5 * sun) * p[3], cw);
    return;
  }
  // final particle
  const q = clamp((t - FP_T0) / (FP_T1 - FP_T0));
  const d = eio(q) * FP.total;
  const fade = 1 - ramp(t, FP_T1 - .2, FP_T1 + .25);
  const TL = 150, NT = 40;
  ex.lineCap = 'round';
  let prev = fpAt(d);
  for (let k = 1; k <= NT; k++) {
    const dd = d - TL * k / NT; if (dd < 0) break;
    const p = fpAt(dd), f = 1 - k / NT;
    ex.strokeStyle = `rgba(255,${(130 + 120 * f) | 0},${(50 + 150 * f * f) | 0},${(f * f * fade).toFixed(3)})`;
    ex.lineWidth = .6 + 2.2 * f;
    ex.beginPath(); ex.moveTo(prev[0], prev[1]); ex.lineTo(p[0], p[1]); ex.stroke();
    prev = p;
  }
  const h = fpAt(d);
  blit(ex, GLOW, h[0], h[1], 40, fade * .7);
  blit(ex, HOT, h[0], h[1], 9, fade);
}

// ───────────────────────── end card ─────────────────────────
function drawEnd(t) {
  if (t < 12.05) return;
  const b = END.brnrd;
  const pB = eox(clamp((t - 12.12) / 1.1)), pH = eox(clamp((t - 12.28) / 1.1));
  // brnrd emerges out of the divider, leftwards
  if (pB > 0) {
    ctx.save();
    ctx.beginPath(); ctx.rect(-600, -600, L.DX - 7 + 600, 1200); ctx.clip();
    ctx.globalAlpha = clamp(pB * 2);
    if (pB < .6) ctx.filter = `blur(${((1 - pB / .6) * 4 * SS).toFixed(2)}px)`;
    ctx.font = `800 ${b.size}px IT`; ctx.letterSpacing = `${b.ls}px`; ctx.fillStyle = CREAM; ctx.textAlign = 'left';
    ctx.fillText('brnrd', END.left + (1 - pB) * (b.width + 20), END.yB);
    ctx.restore();
  }
  // the parent company emerges rightwards
  if (pH > 0) {
    ctx.save();
    ctx.beginPath(); ctx.rect(L.DX + 7, -600, 600, 1200); ctx.clip();
    ctx.globalAlpha = clamp(pH * 2) * .96;
    ctx.drawImage(HMG.c, END.hmX - (1 - pH) * (HMG.w + 20), END.hmY, HMG.w, HMG.h);
    ctx.restore();
  }
  // brnrd.dev outline, the particle's track
  const pa = ramp(t, 12.4, 12.85) * .32;
  if (pa > 0) {
    const { cx, cy, w, h } = END.pill;
    ctx.save();
    ctx.strokeStyle = `rgba(239,229,214,${pa.toFixed(3)})`; ctx.lineWidth = .9;
    ctx.beginPath(); ctx.roundRect(cx - w / 2, cy - h / 2, w, h, h / 2); ctx.stroke();
    ctx.restore();
  }
}

// ───────────────────────── scrims (keep type legible over the system) ─────────────────────────
const SCRIMS = [
  [0, -10, 560, 200, .78, 0, .4, 2.5, 3.0],
  [0, 10, 560, 220, .78, 2.6, 3.0, 4.8, 5.3],
  [0, -380, 560, 120, .75, 4.8, 5.2, 7.4, 7.9],
  [0, -150, 560, 180, .72, 7.4, 7.9, 10.2, 10.6],
  [0, -140, 560, 170, .72, 10.1, 10.5, 11.5, 11.9],
];
function drawScrims(t) {
  for (const [x, y, rx, ry, a, t0, t1, t2, t3] of SCRIMS) {
    const v = ramp(t, t0, t1) * (1 - ramp(t, t2, t3)) * a;
    if (v <= .002) continue;
    ctx.save(); ctx.translate(x, y); ctx.scale(rx / ry, 1);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, ry);
    g.addColorStop(0, `rgba(4,3,2,${v})`); g.addColorStop(.55, `rgba(4,3,2,${v * .7})`); g.addColorStop(1, 'rgba(4,3,2,0)');
    ctx.fillStyle = g; ctx.fillRect(-ry, -ry, ry * 2, ry * 2); ctx.restore();
  }
}

// ───────────────────────── finishing ─────────────────────────
const GRAIN = [0, 1, 2, 3].map(s => {
  const c = mk(1080, 1080), g = c.getContext('2d'), im = g.createImageData(1080, 1080), d = im.data;
  let seed = 1234 + s * 999;
  for (let k = 0; k < d.length; k += 4) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const v = seed >>> 24; d[k] = v; d[k + 1] = v * .92; d[k + 2] = v * .82; d[k + 3] = 255;
  }
  g.putImageData(im, 0, 0); return c;
});
let VIG = null;
function vignette() {
  if (VIG) return VIG;
  const c = mk(PX, PX), g = c.getContext('2d');
  const gr = g.createRadialGradient(PX / 2, PX / 2, PX * .28, PX / 2, PX / 2, PX * .75);
  gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,.62)');
  g.fillStyle = gr; g.fillRect(0, 0, PX, PX); VIG = c; return c;
}

// ───────────────────────── frame ─────────────────────────
function render(t) {
  const Z = kf(ZOOM, t);
  const tf = [SS * Z, 0, 0, SS * Z, PX / 2, PX / 2];
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1; ctx.filter = 'none'; ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = BG; ctx.fillRect(0, 0, PX, PX);
  ex.setTransform(1, 0, 0, 1, 0, 0); ex.globalAlpha = 1; ex.globalCompositeOperation = 'source-over'; ex.clearRect(0, 0, PX, PX);
  ctx.setTransform(...tf); ex.setTransform(...tf);
  ex.globalCompositeOperation = 'lighter';

  const endFade = 1 - ramp(t, 12.4, 13.4) * .75;
  drawNebula(t, endFade);
  drawDust(t, endFade);
  const frames = [];
  for (let i = 0; i < NS; i++) frames.push(frameOf(i, t));
  drawStrands(t, frames);
  drawEnergy(t, frames);
  const jobs = [];
  drawNetwork(t, frames, jobs);
  if (t < FP_T0) drawCore(t);
  for (const l of LINES) if (l.node === undefined && l !== END.brnrd && l !== END.url && l !== END.desc) jobs.push(l.render(t));

  // bloom: two soft passes of the energy layer
  b1.setTransform(1, 0, 0, 1, 0, 0); b1.clearRect(0, 0, B1.width, B1.height);
  b1.filter = 'blur(2.5px)'; b1.drawImage(E, 0, 0, B1.width, B1.height); b1.filter = 'none';
  b2.setTransform(1, 0, 0, 1, 0, 0); b2.clearRect(0, 0, B2.width, B2.height);
  b2.filter = 'blur(4px)'; b2.drawImage(B1, 0, 0, B2.width, B2.height); b2.filter = 'none';
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = .55; ctx.drawImage(B2, 0, 0, PX, PX);
  ctx.globalAlpha = .75; ctx.drawImage(B1, 0, 0, PX, PX);
  ctx.globalAlpha = 1; ctx.drawImage(E, 0, 0);
  ctx.globalCompositeOperation = 'source-over';

  ctx.setTransform(...tf);
  drawScrims(t);
  for (const j of jobs) drawJob(j);
  drawEnd(t);
  // end-card text renders after the lockup
  const endJobs = [];
  if (t > 12) { endJobs.push(END.url.render(t, 0, END.url.y)); endJobs.push(END.desc.render(t, 0, END.desc.y)); }
  for (const j of endJobs) drawJob(j);
  // the final particle sits on top of the type it travels past
  if (t >= FP_T0) {
    ex.setTransform(1, 0, 0, 1, 0, 0); ex.clearRect(0, 0, PX, PX); ex.setTransform(...tf);
    drawCore(t);
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalCompositeOperation = 'lighter';
    b1.clearRect(0, 0, B1.width, B1.height); b1.filter = 'blur(2.5px)'; b1.drawImage(E, 0, 0, B1.width, B1.height); b1.filter = 'none';
    ctx.globalAlpha = .9; ctx.drawImage(B1, 0, 0, PX, PX); ctx.globalAlpha = 1; ctx.drawImage(E, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
  }

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1; ctx.drawImage(vignette(), 0, 0);
  ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = .022;
  ctx.drawImage(GRAIN[Math.floor(t * 60) % 4], 0, 0, PX, PX);
  ctx.globalCompositeOperation = 'source-over';
  const black = 1 - ramp(t, 0, .7) * (1 - ramp(t, 13.5, 13.98));
  if (black > 0) { ctx.globalAlpha = black; ctx.fillStyle = '#000'; ctx.fillRect(0, 0, PX, PX); ctx.globalAlpha = 1; }

  octx.imageSmoothingEnabled = true; octx.imageSmoothingQuality = 'high';
  octx.drawImage(M, 0, 0, W, W);
}

// ───────────────────────── boot ─────────────────────────
window.DUR = DUR;
window.frameAt = t => { render(t); return out.toDataURL('image/png').slice(22); };
window.renderAt = t => render(t);
Promise.all([
  document.fonts.load('700 64px IT'), document.fonts.load('800 64px IT'), document.fonts.load('500 64px IT'),
  document.fonts.load('400 64px IT'), document.fonts.load('400 15px JB'), document.fonts.load('500 15px JB'),
]).then(() => {
  layoutText();
  window.READY = true;
  if (location.hash === '#play') {
    const t0 = performance.now();
    const loop = () => { render(((performance.now() - t0) / 1000) % DUR); requestAnimationFrame(loop); };
    loop();
  } else render(location.hash ? parseFloat(location.hash.slice(1)) || 0 : 0);
});
