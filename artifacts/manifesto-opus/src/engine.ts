// A tiny projective wireframe engine for canvas 2D.
// Every world in the film is geometry pushed through one `Cam`: a free 3x3 linear map
// (lerped element-wise, so in-betweens shear non-rigidly — the "reprojection" look),
// an optional perspective amount, and optional screen-space faults that split geometry
// by side and displace each side by depth (misregistration + depth shear).
export const W = 1920, H = 1080;
export type V3 = [number, number, number];
export type M3 = number[];

export const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const prog = (t: number, a: number, b: number) => clamp((t - a) / (b - a));
export const ss = (a: number, b: number, x: number) => { const t = prog(x, a, b); return t * t * (3 - 2 * t); };
export const eio = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const eout = (t: number) => 1 - Math.pow(1 - clamp(t), 3);
export const ein = (t: number) => Math.pow(clamp(t), 3);
export const expo = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? Math.pow(2, 20 * t - 10) / 2 : (2 - Math.pow(2, -20 * t + 10)) / 2);
export const win = (t: number, a: number, b: number, fi = 0.3, fo = 0.3) => Math.min(ss(a, a + fi, t), 1 - ss(b - fo, b, t));
export const hash = (n: number) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
export function rng(seed: number) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
export const v3 = (x: number, y: number, z: number): V3 => [x, y, z];
export const vadd = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const vsc = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
export const vlerp = (a: V3, b: V3, t: number): V3 => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];

// --- matrices (row-major, world -> view; view x right, y up, z away) ---
export const I3: M3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
export const rotY = (a: number): M3 => { const c = Math.cos(a), s = Math.sin(a); return [c, 0, -s, 0, 1, 0, s, 0, c]; };
export const rotX = (a: number): M3 => { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, c, -s, 0, s, c]; };
export const rotZ = (a: number): M3 => { const c = Math.cos(a), s = Math.sin(a); return [c, -s, 0, s, c, 0, 0, 0, 1]; };
export function mul(A: M3, B: M3): M3 {
  const r: M3 = new Array(9).fill(0);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) r[i * 3 + j] = A[i * 3] * B[j] + A[i * 3 + 1] * B[3 + j] + A[i * 3 + 2] * B[6 + j];
  return r;
}
export const mlerp = (A: M3, B: M3, t: number): M3 => A.map((v, i) => v + (B[i] - v) * t);
// axis permutations: the "which axis is up" swap used by reprojection
export const PERM: Record<string, M3> = {
  xyz: I3,
  yxz: [0, 1, 0, 1, 0, 0, 0, 0, 1],
  xzy: [1, 0, 0, 0, 0, 1, 0, 1, 0],
  zyx: [0, 0, 1, 0, 1, 0, 1, 0, 0],
  yzx: [0, 1, 0, 0, 0, 1, 1, 0, 0],
  zxy: [0, 0, 1, 1, 0, 0, 0, 1, 0],
};
const DEG = Math.PI / 180;
export const view = (yawDeg: number, pitchDeg: number, rollDeg = 0, perm: M3 = I3): M3 => mul(rotZ(rollDeg * DEG), mul(rotX(-pitchDeg * DEG), mul(rotY(yawDeg * DEG), perm)));

export type Cam = { m: M3; t: V3; zoom: number; persp: number; D: number; cx: number; cy: number; shx?: number; shy?: number };
export const cam = (m: M3, t: V3 = [0, 0, 0], zoom = 1, persp = 0, D = 1800, cx = W / 2, cy = H / 2): Cam => ({ m, t, zoom, persp, D, cx, cy, shx: 0, shy: 0 });
export const camLerp = (a: Cam, b: Cam, k: number): Cam => ({
  m: mlerp(a.m, b.m, k), t: vlerp(a.t, b.t, k), zoom: Math.exp(lerp(Math.log(a.zoom), Math.log(b.zoom), k)), persp: lerp(a.persp, b.persp, k), D: lerp(a.D, b.D, k),
  cx: lerp(a.cx, b.cx, k), cy: lerp(a.cy, b.cy, k), shx: lerp(a.shx || 0, b.shx || 0, k), shy: lerp(a.shy || 0, b.shy || 0, k),
});

export type Fault = { x: number; y: number; a: number; shift: number; gap?: number; shear?: number; wob?: number; band?: number };

// --- the renderer ---
type Bucket = { col: string; w: number; a: number; pts: number[] };
type Poly = { pts: number[]; fill: string; a: number };
type Txt = { s: string; x: number; y: number; font: string; col: string; a: number; align: CanvasTextAlign; tr?: number[]; base?: CanvasTextBaseline; track?: number };
type Dot = { x: number; y: number; r: number; col: string; a: number; shape: number };

export class R {
  cam: Cam = cam(I3);
  faults: Fault[] = [];
  warp: ((p: V3) => V3) | null = null;
  buckets = new Map<string, Bucket>();
  polys: Poly[] = [];
  texts: Txt[] = [];
  dots: Dot[] = [];
  fringe: number[] = []; // x1,y1,x2,y2,nx,ny for diffraction echoes
  alpha = 1; // global multiplier
  constructor(public ctx: CanvasRenderingContext2D) {}

  P(p: V3): [number, number, number, number] {
    if (this.warp) p = this.warp(p);
    const c = this.cam, m = c.m;
    const dx = p[0] - c.t[0], dy = p[1] - c.t[1], dz = p[2] - c.t[2];
    let X = m[0] * dx + m[1] * dy + m[2] * dz, Y = m[3] * dx + m[4] * dy + m[5] * dz;
    const Z = m[6] * dx + m[7] * dy + m[8] * dz;
    X += (c.shx || 0) * Z; Y += (c.shy || 0) * Z;
    const den = 1 + (c.persp * Z) / c.D;
    const k = den > 0.04 ? 1 / den : -1;
    return [c.cx + X * k * c.zoom, c.cy - Y * k * c.zoom, Z, k];
  }
  side(f: Fault, x: number, y: number) { return -Math.sin(f.a) * (x - f.x) + Math.cos(f.a) * (y - f.y) >= 0 ? 1 : -1; }
  disp(x: number, y: number, Z: number): [number, number] {
    let ox = 0, oy = 0;
    for (const f of this.faults) {
      const s = this.side(f, x, y), dx = Math.cos(f.a), dy = Math.sin(f.a);
      const along = dx * (x - f.x) + dy * (y - f.y);
      const sh = f.shift * 0.5 + (f.shear || 0) * Z * 0.5 + (f.wob || 0) * Math.sin(along * 0.013 + s);
      const g = (f.gap || 0) * 0.5;
      ox += s * (sh * dx - g * dy); oy += s * (sh * dy + g * dx);
    }
    return [ox, oy];
  }
  key(col: string, w: number, a: number) {
    const aq = Math.round(clamp(a * this.alpha) * 40) / 40;
    const k = col + '|' + w.toFixed(2) + '|' + aq;
    let b = this.buckets.get(k);
    if (!b) { b = { col, w, a: aq, pts: [] }; this.buckets.set(k, b); }
    return b;
  }
  // screen-space segment with fault splitting
  seg2(ax: number, ay: number, az: number, bx: number, by: number, bz: number, col: string, w: number, a: number) {
    if (a * this.alpha < 0.012) return;
    const b = this.key(col, w, a);
    if (!this.faults.length) { b.pts.push(ax, ay, bx, by); return; }
    const us: number[] = [0, 1];
    for (const f of this.faults) {
      const na = -Math.sin(f.a) * (ax - f.x) + Math.cos(f.a) * (ay - f.y);
      const nb = -Math.sin(f.a) * (bx - f.x) + Math.cos(f.a) * (by - f.y);
      if ((na >= 0) !== (nb >= 0)) us.push(na / (na - nb));
    }
    us.sort((p, q) => p - q);
    for (let i = 0; i < us.length - 1; i++) {
      const u0 = us[i], u1 = us[i + 1]; if (u1 - u0 < 1e-5) continue;
      const um = (u0 + u1) / 2;
      const mx = lerp(ax, bx, um), my = lerp(ay, by, um), mz = lerp(az, bz, um);
      const [ox, oy] = this.disp(mx, my, mz);
      const x0 = lerp(ax, bx, u0) + ox, y0 = lerp(ay, by, u0) + oy, x1 = lerp(ax, bx, u1) + ox, y1 = lerp(ay, by, u1) + oy;
      b.pts.push(x0, y0, x1, y1);
      // a split end touches the fracture: leave a diffraction echo
      if (us.length > 2 && (i > 0 || i < us.length - 2)) {
        const atStart = i > 0; const ex = atStart ? x0 : x1, ey = atStart ? y0 : y1, fx = atStart ? x1 : x0, fy = atStart ? y1 : y0;
        const L = Math.hypot(fx - ex, fy - ey) || 1, k = Math.min(1, 26 / L);
        this.fringe.push(ex, ey, lerp(ex, fx, k), lerp(ey, fy, k), a);
      }
    }
  }
  line(a: V3, b: V3, col: string, w = 1, al = 1) {
    const A = this.P(a), B = this.P(b);
    if (A[3] < 0 || B[3] < 0) return;
    this.seg2(A[0], A[1], A[2], B[0], B[1], B[2], col, w, al);
  }
  // polyline, optionally partially drawn (0..1 of its length)
  path(ps: V3[], col: string, w = 1, al = 1, draw = 1, dash = 0) {
    if (draw <= 0 || ps.length < 2) return;
    const L: number[] = [0];
    for (let i = 1; i < ps.length; i++) L.push(L[i - 1] + Math.hypot(ps[i][0] - ps[i - 1][0], ps[i][1] - ps[i - 1][1], ps[i][2] - ps[i - 1][2]));
    const tot = L[L.length - 1] * clamp(draw);
    for (let i = 1; i < ps.length; i++) {
      if (L[i - 1] >= tot) break;
      const k = clamp((tot - L[i - 1]) / (L[i] - L[i - 1] || 1));
      const e = vlerp(ps[i - 1], ps[i], k);
      if (dash > 0) {
        const n = Math.max(1, Math.floor((L[i] - L[i - 1]) * k / dash));
        for (let j = 0; j < n; j += 2) this.line(vlerp(ps[i - 1], e, j / n), vlerp(ps[i - 1], e, Math.min(1, (j + 1) / n)), col, w, al);
      } else this.line(ps[i - 1], e, col, w, al);
    }
  }
  pathEnd(ps: V3[], draw: number): V3 {
    const L: number[] = [0];
    for (let i = 1; i < ps.length; i++) L.push(L[i - 1] + Math.hypot(ps[i][0] - ps[i - 1][0], ps[i][1] - ps[i - 1][1], ps[i][2] - ps[i - 1][2]));
    const tot = L[L.length - 1] * clamp(draw);
    for (let i = 1; i < ps.length; i++) if (L[i] >= tot) return vlerp(ps[i - 1], ps[i], clamp((tot - L[i - 1]) / (L[i] - L[i - 1] || 1)));
    return ps[ps.length - 1];
  }
  box(a: V3, b: V3, col: string, w = 1, al = 1, draw = 1) {
    const [x0, y0, z0] = a, [x1, y1, z1] = b;
    const e: [V3, V3][] = [
      [[x0, y0, z0], [x1, y0, z0]], [[x1, y0, z0], [x1, y0, z1]], [[x1, y0, z1], [x0, y0, z1]], [[x0, y0, z1], [x0, y0, z0]],
      [[x0, y0, z0], [x0, y1, z0]], [[x1, y0, z0], [x1, y1, z0]], [[x1, y0, z1], [x1, y1, z1]], [[x0, y0, z1], [x0, y1, z1]],
      [[x0, y1, z0], [x1, y1, z0]], [[x1, y1, z0], [x1, y1, z1]], [[x1, y1, z1], [x0, y1, z1]], [[x0, y1, z1], [x0, y1, z0]],
    ];
    const n = e.length * clamp(draw);
    e.forEach(([p, q], i) => { if (i < n) this.line(p, i + 1 > n ? vlerp(p, q, n - i) : q, col, w, al); });
  }
  poly(ps: V3[], fill: string, a: number) {
    if (a * this.alpha < 0.01) return;
    const out: number[] = [];
    for (const p of ps) {
      const q = this.P(p); if (q[3] < 0) return;
      const [ox, oy] = this.faults.length ? this.disp(q[0], q[1], q[2]) : [0, 0];
      out.push(q[0] + ox, q[1] + oy);
    }
    this.polys.push({ pts: out, fill, a: a * this.alpha });
  }
  dot(p: V3, r: number, col: string, a = 1, shape = 0) {
    const q = this.P(p); if (q[3] < 0) return;
    const [ox, oy] = this.faults.length ? this.disp(q[0], q[1], q[2]) : [0, 0];
    this.dots.push({ x: q[0] + ox, y: q[1] + oy, r: r * Math.min(2.5, q[3]), col, a: a * this.alpha, shape });
  }
  // screen-facing text anchored at a world point
  label(s: string, p: V3, font: string, col: string, a = 1, align: CanvasTextAlign = 'left', dx = 0, dy = 0, track = 0) {
    if (a * this.alpha < 0.01) return;
    const q = this.P(p); if (q[3] < 0) return;
    const [ox, oy] = this.faults.length ? this.disp(q[0], q[1], q[2]) : [0, 0];
    this.texts.push({ s, x: q[0] + ox + dx, y: q[1] + oy + dy, font, col, a: a * this.alpha, align, track });
  }
  // text lying on a world plane spanned by unit vectors u (baseline) and v (up), px-per-unit `size`
  text3d(s: string, o: V3, u: V3, v: V3, font: string, col: string, a = 1, align: CanvasTextAlign = 'left', track = 0) {
    if (a * this.alpha < 0.01) return;
    const O = this.P(o), U = this.P(vadd(o, u)), V = this.P(vadd(o, v));
    if (O[3] < 0 || U[3] < 0 || V[3] < 0) return;
    const [ox, oy] = this.faults.length ? this.disp(O[0], O[1], O[2]) : [0, 0];
    this.texts.push({ s, x: 0, y: 0, font, col, a: a * this.alpha, align, track, tr: [U[0] - O[0], U[1] - O[1], -(V[0] - O[0]), -(V[1] - O[1]), O[0] + ox, O[1] + oy] });
  }
  screenText(s: string, x: number, y: number, font: string, col: string, a = 1, align: CanvasTextAlign = 'left', track = 0, base: CanvasTextBaseline = 'alphabetic') {
    if (a * this.alpha < 0.01) return;
    this.texts.push({ s, x, y, font, col, a: a * this.alpha, align, track, base });
  }
  flush(additive = true) {
    const c = this.ctx;
    c.save();
    for (const p of this.polys) {
      c.globalAlpha = p.a; c.fillStyle = p.fill; c.beginPath();
      c.moveTo(p.pts[0], p.pts[1]); for (let i = 2; i < p.pts.length; i += 2) c.lineTo(p.pts[i], p.pts[i + 1]);
      c.closePath(); c.fill();
    }
    c.globalAlpha = 1;
    c.globalCompositeOperation = additive ? 'lighter' : 'source-over';
    c.lineCap = 'round';
    for (const b of this.buckets.values()) {
      if (!b.pts.length) continue;
      c.strokeStyle = `rgba(${b.col},${b.a})`; c.lineWidth = b.w; c.beginPath();
      for (let i = 0; i < b.pts.length; i += 4) { c.moveTo(b.pts[i], b.pts[i + 1]); c.lineTo(b.pts[i + 2], b.pts[i + 3]); }
      c.stroke();
    }
    // diffraction echoes at every split end: thin, offset, spectral — never a band
    if (this.fringe.length) {
      const F = this.fringe;
      const tints = ['255,40,90', '40,255,210', '120,90,255'];
      tints.forEach((tint, ti) => {
        const off = (ti - 1) * 2.2;
        c.strokeStyle = `rgba(${tint},0.55)`; c.lineWidth = 0.8; c.beginPath();
        for (let i = 0; i < F.length; i += 5) {
          const dx = F[i + 2] - F[i], dy = F[i + 3] - F[i + 1], L = Math.hypot(dx, dy) || 1;
          const nx = -dy / L * off, ny = dx / L * off;
          c.moveTo(F[i] + nx, F[i + 1] + ny); c.lineTo(F[i + 2] + nx, F[i + 3] + ny);
        }
        c.stroke();
      });
    }
    for (const d of this.dots) {
      c.globalAlpha = clamp(d.a); c.fillStyle = `rgb(${d.col})`; c.strokeStyle = `rgb(${d.col})`;
      if (d.shape === 0) { c.beginPath(); c.arc(d.x, d.y, d.r, 0, Math.PI * 2); c.fill(); }
      else if (d.shape === 1) c.fillRect(d.x - d.r, d.y - d.r, d.r * 2, d.r * 2);
      else if (d.shape === 2) { c.lineWidth = 1.2; c.strokeRect(d.x - d.r, d.y - d.r, d.r * 2, d.r * 2); }
      else if (d.shape === 3) { c.lineWidth = 1.4; c.beginPath(); c.arc(d.x, d.y, d.r, 0, Math.PI * 2); c.stroke(); }
    }
    c.globalCompositeOperation = 'source-over';
    for (const t of this.texts) {
      c.globalAlpha = clamp(t.a); c.fillStyle = t.col; c.font = t.font; c.textAlign = t.align; c.textBaseline = t.base || 'alphabetic';
      (c as any).letterSpacing = (t.track || 0) + 'px';
      if (t.tr) { c.save(); c.transform(t.tr[0], t.tr[1], t.tr[2], t.tr[3], t.tr[4], t.tr[5]); c.fillText(t.s, 0, 0); c.restore(); }
      else c.fillText(t.s, t.x, t.y);
    }
    (c as any).letterSpacing = '0px';
    c.restore();
    this.buckets.clear(); this.polys = []; this.texts = []; this.dots = []; this.fringe = [];
  }
}

// --- offscreen helpers ---
const pool = new Map<string, HTMLCanvasElement>();
export function off(name: string, w = W, h = H) {
  let c = pool.get(name);
  if (!c) { c = document.createElement('canvas'); pool.set(name, c); }
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return c;
}
export const ctx2 = (c: HTMLCanvasElement) => c.getContext('2d', { willReadFrequently: false }) as CanvasRenderingContext2D;

// bloom: downsample, blur, add back
export function bloom(ctx: CanvasRenderingContext2D, amt = 0.8, rad = 10) {
  if (amt <= 0) return;
  const s = off('bloom', W / 4, H / 4), sc = ctx2(s);
  sc.globalCompositeOperation = 'copy'; sc.filter = `blur(${rad / 4}px)`; sc.drawImage(ctx.canvas, 0, 0, W / 4, H / 4); sc.filter = 'none';
  ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = amt; ctx.imageSmoothingEnabled = true; ctx.drawImage(s, 0, 0, W, H); ctx.restore();
}

// bitmapify: quantise a canvas to a palette on a coarse grid with ordered dither, upscale nearest
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16 - 0.5);
export function bitmapify(src: HTMLCanvasElement, dst: CanvasRenderingContext2D, block: number, pal: number[][], ditherAmt = 0.18, mix = 1) {
  const w = Math.max(1, Math.round(W / block)), h = Math.max(1, Math.round(H / block));
  const s = off('bmp', w, h), sc = s.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  sc.imageSmoothingEnabled = true; sc.globalCompositeOperation = 'copy'; sc.drawImage(src, 0, 0, w, h);
  const img = sc.getImageData(0, 0, w, h), d = img.data;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4; const th = BAYER[(y & 3) * 4 + (x & 3)] * ditherAmt * 255;
    const r = d[i] + th, g = d[i + 1] + th, b = d[i + 2] + th;
    let best = 0, bd = 1e12;
    for (let p = 0; p < pal.length; p++) { const q = pal[p]; const e = (r - q[0]) ** 2 * 0.3 + (g - q[1]) ** 2 * 0.59 + (b - q[2]) ** 2 * 0.11; if (e < bd) { bd = e; best = p; } }
    const q = pal[best]; d[i] = lerp(d[i], q[0], mix); d[i + 1] = lerp(d[i + 1], q[1], mix); d[i + 2] = lerp(d[i + 2], q[2], mix);
  }
  sc.putImageData(img, 0, 0);
  dst.save(); dst.imageSmoothingEnabled = false; dst.globalCompositeOperation = 'copy'; dst.drawImage(s, 0, 0, W, H); dst.restore();
}

// image-space tear: split the frame along a line, slide the halves apart, lace the seam with thin diffraction
export function tear(ctx: CanvasRenderingContext2D, f: Fault, t: number, fringe = 1) {
  const src = off('tear'); const sc = ctx2(src);
  sc.globalCompositeOperation = 'copy'; sc.drawImage(ctx.canvas, 0, 0);
  const dx = Math.cos(f.a), dy = Math.sin(f.a), nx = -dy, ny = dx, BIG = 5000;
  const g = (f.gap || 0) / 2;
  ctx.save(); ctx.globalCompositeOperation = 'copy'; ctx.fillStyle = 'rgba(0,0,0,0)'; ctx.fillRect(0, 0, W, H); ctx.restore();
  for (const s of [1, -1]) {
    ctx.save(); ctx.beginPath();
    ctx.moveTo(f.x - dx * BIG, f.y - dy * BIG); ctx.lineTo(f.x + dx * BIG, f.y + dy * BIG);
    ctx.lineTo(f.x + dx * BIG + nx * BIG * s, f.y + dy * BIG + ny * BIG * s); ctx.lineTo(f.x - dx * BIG + nx * BIG * s, f.y - dy * BIG + ny * BIG * s);
    ctx.closePath(); ctx.clip();
    ctx.drawImage(src, s * (f.shift / 2 * dx + g * nx), s * (f.shift / 2 * dy + g * ny));
    ctx.restore();
  }
  if (fringe > 0) fracture(ctx, f, t, fringe);
}
// the seam itself: a white hairline core, spectral hairlines offset a pixel or two, fine interference ticks
export function fracture(ctx: CanvasRenderingContext2D, f: Fault, t: number, amt = 1, len = 5000) {
  const dx = Math.cos(f.a), dy = Math.sin(f.a), nx = -dy, ny = dx;
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  const L = (o: number, col: string, w: number) => { ctx.strokeStyle = col; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(f.x - dx * len + nx * o, f.y - dy * len + ny * o); ctx.lineTo(f.x + dx * len + nx * o, f.y + dy * len + ny * o); ctx.stroke(); };
  L(0, `rgba(255,255,255,${0.9 * amt})`, 1);
  L(2.5, `rgba(255,50,110,${0.5 * amt})`, 0.8); L(-2.5, `rgba(60,255,220,${0.5 * amt})`, 0.8); L(5, `rgba(130,100,255,${0.3 * amt})`, 0.6); L(-6, `rgba(255,190,60,${0.2 * amt})`, 0.6);
  // interference: short perpendicular ticks whose hue walks slowly along the seam
  const r = rng(Math.floor(t * 30) * 7 + 3);
  for (let i = 0; i < 90; i++) {
    const along = (r() - 0.5) * 2400, hl = 2 + r() * 10, hue = (along * 0.15 + t * 90) % 360;
    const px = f.x + dx * along, py = f.y + dy * along, o = (r() - 0.5) * 8;
    ctx.strokeStyle = `hsla(${hue},100%,70%,${0.45 * amt * r()})`; ctx.lineWidth = 0.7; ctx.beginPath();
    ctx.moveTo(px + nx * (o - hl), py + ny * (o - hl)); ctx.lineTo(px + nx * (o + hl), py + ny * (o + hl)); ctx.stroke();
  }
  ctx.restore();
}

// the resident: drawn in screen space, outside every projection — identical in every frame it appears
export function resident(ctx: CanvasRenderingContext2D, x: number, y: number, a = 1, t = 0, halo = 1) {
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha = a; ctx.translate(Math.round(x) + 0.5, Math.round(y) + 0.5);
  if (halo > 0) {
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 90);
    g.addColorStop(0, `rgba(255,255,255,${0.22 * halo})`); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, 90, 0, Math.PI * 2); ctx.fill();
  }
  ctx.globalCompositeOperation = 'lighter';
  ['255,40,100', '40,255,220', '140,110,255'].forEach((c, i) => {
    ctx.strokeStyle = `rgba(${c},0.6)`; ctx.lineWidth = 0.8; ctx.beginPath(); ctx.arc((i - 1) * 1.1, (1 - i) * 0.7, 25, 0, Math.PI * 2); ctx.stroke();
  });
  ctx.globalCompositeOperation = 'source-over';
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(0, -17); ctx.lineTo(17, 0); ctx.lineTo(0, 17); ctx.lineTo(-17, 0); ctx.closePath(); ctx.stroke();
  ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(0, 0, 5, 0, Math.PI * 2); ctx.fill();
  ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(-36, 0); ctx.lineTo(-24, 0); ctx.moveTo(24, 0); ctx.lineTo(36, 0); ctx.stroke();
  ctx.restore();
}

export function grain(ctx: CanvasRenderingContext2D, t: number, amt: number, dark = true) {
  const g = off('grain', 480, 270), gc = g.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  const img = gc.createImageData(480, 270), d = img.data, r = rng(Math.floor(t * 30) * 13 + 1);
  for (let i = 0; i < d.length; i += 4) { const v = r() * 255; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; }
  gc.putImageData(img, 0, 0);
  ctx.save(); ctx.globalAlpha = amt; ctx.globalCompositeOperation = dark ? 'overlay' : 'multiply'; ctx.imageSmoothingEnabled = true; ctx.drawImage(g, 0, 0, W, H); ctx.restore();
}
export function vignette(ctx: CanvasRenderingContext2D, amt = 0.6, col = '0,0,0') {
  const g = ctx.createRadialGradient(W / 2, H / 2, H * 0.35, W / 2, H / 2, H * 1.05);
  g.addColorStop(0, `rgba(${col},0)`); g.addColorStop(1, `rgba(${col},${amt})`);
  ctx.save(); ctx.fillStyle = g; ctx.fillRect(0, 0, W, H); ctx.restore();
}
