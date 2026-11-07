// The brnrd pixel wordmark: five letters on a coarse grid, one square cell per
// pixel. Pure functions, no DOM, so the site wordmark or a video can import it.
//
// Rules (media/brand/README.md, the five-slot mark): slot 1 is b, slot 5 is d,
// slots 2-4 are the state. The awake frame b^n^d is the resting word with the
// n lowered and the r stems taken away; the ^ eye is the r's own spur. So every
// letter keeps its slot and width in both frames and only parts move:
//   b, d  a bar (ascender) plus a bowl, mirrored.
//   r     a ^ (apex on top) whose left foot continues as a leg to the baseline.
//   n     arch, legs on the baseline; awake, its top drops by `nDrop` rows.
//   eyes  the two r's. Awake: leg gone. Wink frames swap a ^ for a dash.

export interface Params {
	cell: number; // px per cell in the main view and exports
	radius: number; // cell corner radius, fraction of the cell (0 square .. 0.5 round)
	gap: number; // gap between cells, fraction of the cell
	rows: number; // grid height in cells
	bar: number; // height of the b/d bar, cells (clamped to rows)
	xh: number; // x-height of r and n, cells (clamped to bar)
	bowl: number; // width of the b/d bowl, cells (including the bar column)
	caret: number; // width of the ^, cells (odd reads best)
	leg: number; // r leg length below the ^, cells
	nDrop: number; // how far the n drops in the awake frame, rows
	space: number; // gap between letters, cells
}

export const DEFAULTS: Params = {
	cell: 22,
	radius: 0.22,
	gap: 0.08,
	rows: 10,
	bar: 10,
	xh: 6,
	bowl: 4,
	caret: 3,
	leg: 4,
	nDrop: 3,
	space: 1
};

export interface Range {
	key: keyof Params;
	label: string;
	min: number;
	max: number;
	step: number;
}

export const RANGES: Range[] = [
	{ key: 'cell', label: 'cell size (px)', min: 4, max: 48, step: 1 },
	{ key: 'radius', label: 'corner radius', min: 0, max: 0.5, step: 0.01 },
	{ key: 'gap', label: 'gap', min: 0, max: 0.4, step: 0.01 },
	{ key: 'rows', label: 'grid height (cells)', min: 5, max: 16, step: 1 },
	{ key: 'bar', label: 'bar length, b and d', min: 4, max: 16, step: 1 },
	{ key: 'xh', label: 'x-height, r and n', min: 3, max: 12, step: 1 },
	{ key: 'bowl', label: 'bowl width, b and d', min: 3, max: 7, step: 1 },
	{ key: 'caret', label: '^ width', min: 3, max: 9, step: 2 },
	{ key: 'leg', label: 'r leg length', min: 0, max: 10, step: 1 },
	{ key: 'nDrop', label: 'n offset (awake)', min: 0, max: 8, step: 1 },
	{ key: 'space', label: 'letter spacing', min: 0, max: 3, step: 1 }
];

export type Eye = 'caret' | 'dash';
export interface Pose {
	t: number; // 0 resting .. 1 awake: n lowered, legs gone
	eyeL: Eye;
	eyeR: Eye;
}
export const REST: Pose = { t: 0, eyeL: 'caret', eyeR: 'caret' };
export const AWAKE: Pose = { t: 1, eyeL: 'caret', eyeR: 'caret' };

/** The wink, from WinkWordmark.svelte: rest, b-n-d, b^n-d, b^n^d, each with its hold (ms). */
export const WINK: Array<{ name: string; pose: Pose; ms: number }> = [
	{ name: 'brnrd', pose: REST, ms: 900 },
	{ name: 'b-n-d', pose: { t: 1, eyeL: 'dash', eyeR: 'dash' }, ms: 220 },
	{ name: 'b^n-d', pose: { t: 1, eyeL: 'caret', eyeR: 'dash' }, ms: 220 },
	{ name: 'b^n^d', pose: AWAKE, ms: 1400 }
];

export interface Cell {
	x: number;
	y: number;
	eye: boolean; // an r's ^ or dash: the accent colour
}
export interface Layout {
	cols: number;
	rows: number;
	cells: Cell[];
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Normalise a param set so the rules above always hold. */
export function fit(p: Params): Params {
	const rows = Math.round(clamp(p.rows, 5, 16));
	const bar = Math.round(clamp(p.bar, 4, rows));
	const xh = Math.round(clamp(p.xh, 3, bar));
	const caret = Math.round(clamp(p.caret, 3, 9));
	return { ...p, rows, bar, xh, caret, bowl: Math.round(clamp(p.bowl, 3, 7)) };
}

export function layout(params: Params, pose: Pose = REST): Layout {
	const p = fit(params);
	const base = p.rows - 1; // baseline row
	const top = p.rows - p.xh; // x-height row
	const cells: Cell[] = [];
	const put = (x: number, y: number, eye = false) => cells.push({ x, y, eye });
	let x0 = 0;

	// b: bar on the left, bowl to its right.
	const bowl = (at: number, mirror: boolean) => {
		const col = (i: number) => (mirror ? at + p.bowl - 1 - i : at + i);
		for (let y = p.rows - p.bar; y <= base; y++) put(col(0), y);
		for (let i = 1; i < p.bowl - 1; i++) {
			put(col(i), top);
			put(col(i), base);
		}
		for (let y = top + 1; y < base; y++) put(col(p.bowl - 1), y);
	};
	bowl(x0, false);
	x0 += p.bowl + p.space;

	// r: a ^ at the x-height, its left foot continued as a leg.
	const r = (at: number, eye: Eye) => {
		const rise = (p.caret + 1) / 2; // rows the ^ spans
		if (eye === 'dash') {
			for (let i = 0; i < p.caret; i++) put(at + i, top + rise - 1, true);
		} else {
			const mid = (p.caret - 1) / 2;
			for (let k = 0; k < rise; k++) {
				put(at + mid - k, top + k, true);
				if (k > 0) put(at + mid + k, top + k, true);
			}
		}
		// leg: from the ^ foot down; vanishes bottom-first as t rises.
		const legTop = top + rise;
		const maxLeg = Math.min(p.leg, base - legTop + 1);
		const keep = Math.round(maxLeg * (1 - pose.t));
		for (let k = 0; k < keep; k++) put(at, legTop + k);
	};
	r(x0, pose.eyeL);
	x0 += p.caret + p.space;

	// n: arch; awake, the top drops by nDrop rows (feet stay on the baseline).
	const nTop = top + Math.round(Math.min(p.nDrop, p.xh - 2) * pose.t);
	for (let y = nTop + 1; y <= base; y++) {
		put(x0, y);
		put(x0 + 3, y);
	}
	put(x0, nTop);
	put(x0 + 1, nTop);
	put(x0 + 2, nTop);
	put(x0 + 3, nTop);
	x0 += 4 + p.space;

	r(x0, pose.eyeR);
	x0 += p.caret + p.space;

	bowl(x0, true);
	x0 += p.bowl;

	// a cell can be emitted twice (n corners, ^ apex); keep the first.
	const seen = new Set<string>();
	const uniq = cells.filter((c) => {
		const k = `${c.x},${c.y}`;
		if (seen.has(k)) return false;
		seen.add(k);
		return true;
	});
	return { cols: x0, rows: p.rows, cells: uniq };
}

export interface Palette {
	name: string;
	fg: string;
	bg: string;
	accent: string;
}

export const PALETTES: Palette[] = [
	{ name: 'ink on paper', fg: '#1b1a17', bg: '#f1ebdd', accent: '#d9480f' },
	{ name: 'molten on stone', fg: '#ff7a1c', bg: '#2a2825', accent: '#ffd9a8' },
	{ name: 'amber on near-black', fg: '#ffb000', bg: '#0d0b07', accent: '#ffe08a' },
	{ name: 'orange on dark', fg: '#ff6a00', bg: '#0b0b10', accent: '#ff6a00' },
	{ name: 'blue on dark', fg: '#4da3ff', bg: '#060a14', accent: '#bfe0ff' },
	{ name: 'mono', fg: '#ffffff', bg: '#000000', accent: '#ffffff' }
];

export interface Effects {
	glow: boolean;
	scan: boolean;
	grain: boolean;
	jitter: boolean;
}
export const NO_FX: Effects = { glow: false, scan: false, grain: false, jitter: false };

export interface Render {
	pose: Pose;
	palette: Pick<Palette, 'fg' | 'bg' | 'accent'>;
	fx: Effects;
	/** Frame: 'fit' hugs the mark; 'square' and 'wide' put it on a tile with padding. */
	frame?: 'fit' | 'square' | 'wide';
	/** 'circle' clips the background to a circle (the avatar). */
	shape?: 'rect' | 'circle';
	/** Rendered pixel height; width follows. Defaults to the params' cell size. */
	height?: number;
	/** Unique id prefix when several SVGs share a page. */
	id?: string;
	/** Draw the background (false = transparent). */
	background?: boolean;
}

const hash = (x: number, y: number) => {
	let h = (x * 374761393 + y * 668265263) | 0;
	h = Math.imul(h ^ (h >>> 13), 1274126177);
	return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
};

export function toSvg(params: Params, r: Render): string {
	const p = fit(params);
	const lay = layout(p, r.pose);
	const id = r.id ?? 'bl';
	const pad = r.frame === 'fit' || !r.frame ? 1 : r.frame === 'wide' ? 0 : 0;
	// logical canvas in cell units
	let w = lay.cols + 2 * pad;
	let h = lay.rows + 2 * pad;
	if (r.frame === 'square') {
		const s = Math.max(lay.cols, lay.rows) + 2;
		w = h = s;
	} else if (r.frame === 'wide') {
		w = Math.ceil(lay.cols * 1.6) + 2;
		h = Math.ceil(lay.rows * 1.6);
		w = Math.max(w, Math.ceil(h * 3));
	}
	const ox = (w - lay.cols) / 2;
	const oy = (h - lay.rows) / 2;
	const px = r.height ?? p.cell * lay.rows + 2 * p.cell * pad;
	const wpx = (px * w) / h;
	const inset = p.gap / 2;
	const size = 1 - p.gap;
	const rx = p.radius * size;
	const bgRect =
		r.background === false
			? ''
			: r.shape === 'circle'
				? `<circle cx="${w / 2}" cy="${h / 2}" r="${Math.min(w, h) / 2}" fill="${r.palette.bg}"/>`
				: `<rect width="${w}" height="${h}" fill="${r.palette.bg}"/>`;
	const rects = lay.cells
		.map((c) => {
			const j = r.fx.jitter ? 0.55 + 0.45 * hash(c.x, c.y) : 1;
			const fill = c.eye ? r.palette.accent : r.palette.fg;
			const op = j < 1 ? ` fill-opacity="${j.toFixed(2)}"` : '';
			return `<rect x="${(ox + c.x + inset).toFixed(3)}" y="${(oy + c.y + inset).toFixed(3)}" width="${size.toFixed(3)}" height="${size.toFixed(3)}" rx="${rx.toFixed(3)}" fill="${fill}"${op}/>`;
		})
		.join('');
	const defs: string[] = [];
	if (r.fx.glow)
		defs.push(
			`<filter id="${id}g" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="${(0.45).toFixed(2)}" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`
		);
	if (r.fx.grain)
		defs.push(
			`<filter id="${id}n" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="${(2.2 / (px / h)).toFixed(3)}" numOctaves="2" seed="7"/><feColorMatrix values="0 0 0 0 0.5  0 0 0 0 0.5  0 0 0 0 0.5  0 0 0 0.35 0"/></filter>`
		);
	if (r.fx.scan)
		defs.push(
			`<pattern id="${id}s" width="1" height="${(1 / 3).toFixed(4)}" patternUnits="userSpaceOnUse"><rect width="1" height="${(1 / 6).toFixed(4)}" fill="#000" fill-opacity="0.28"/></pattern>`
		);
	const clip =
		r.shape === 'circle'
			? `<clipPath id="${id}c"><circle cx="${w / 2}" cy="${h / 2}" r="${Math.min(w, h) / 2}"/></clipPath>`
			: '';
	const body =
		`<g${r.fx.glow ? ` filter="url(#${id}g)"` : ''}>${rects}</g>` +
		(r.fx.scan ? `<rect width="${w}" height="${h}" fill="url(#${id}s)"/>` : '') +
		(r.fx.grain ? `<rect width="${w}" height="${h}" filter="url(#${id}n)"/>` : '');
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${wpx.toFixed(1)}" height="${px}" shape-rendering="geometricPrecision">` +
		(defs.length || clip ? `<defs>${defs.join('')}${clip}</defs>` : '') +
		(r.shape === 'circle' ? `<g clip-path="url(#${id}c)">${bgRect}${body}</g>` : bgRect + body) +
		`</svg>`
	);
}

// ---- URL / JSON encoding: only what differs from the defaults ----

export interface Settings {
	p: Params;
	pose: Pose;
	fg: string;
	bg: string;
	accent: string;
	fx: Effects;
}

export const FX_KEYS = ['glow', 'scan', 'grain', 'jitter'] as const;

export function toQuery(s: Settings, base: Settings): string {
	const q = new URLSearchParams();
	for (const k of Object.keys(DEFAULTS) as (keyof Params)[])
		if (s.p[k] !== base.p[k]) q.set(k, String(s.p[k]));
	if (s.pose.t !== base.pose.t) q.set('t', String(s.pose.t));
	if (s.pose.eyeL !== base.pose.eyeL) q.set('eyeL', s.pose.eyeL);
	if (s.pose.eyeR !== base.pose.eyeR) q.set('eyeR', s.pose.eyeR);
	for (const k of ['fg', 'bg', 'accent'] as const)
		if (s[k] !== base[k]) q.set(k, s[k].replace('#', ''));
	const fx = FX_KEYS.filter((k) => s.fx[k]).join(',');
	if (fx) q.set('fx', fx);
	return q.toString();
}

export function fromQuery(q: URLSearchParams, base: Settings): Settings {
	const s: Settings = { ...base, p: { ...base.p }, pose: { ...base.pose }, fx: { ...NO_FX } };
	for (const k of Object.keys(DEFAULTS) as (keyof Params)[]) {
		const v = Number(q.get(k));
		if (q.has(k) && Number.isFinite(v)) s.p[k] = v;
	}
	const t = Number(q.get('t'));
	if (q.has('t') && Number.isFinite(t)) s.pose.t = clamp(t, 0, 1);
	for (const k of ['eyeL', 'eyeR'] as const) {
		const v = q.get(k);
		if (v === 'caret' || v === 'dash') s.pose[k] = v;
	}
	for (const k of ['fg', 'bg', 'accent'] as const) {
		const v = q.get(k);
		if (v && /^[0-9a-f]{6}$/i.test(v)) s[k] = `#${v}`;
	}
	for (const k of (q.get('fx') ?? '').split(','))
		if ((FX_KEYS as readonly string[]).includes(k)) s.fx[k as keyof Effects] = true;
	return s;
}
