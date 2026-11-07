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
	nStyle: number; // 0 arch (corner cells cut), 1 mirror (the r and its reflection)
	density: number; // 1..4: pixels per cell side; the letter keeps its shape, the pixels shrink
	margin: number; // room around the mark, in cells
	nudgeX: number; // shift the mark inside its box, in cells
	nudgeY: number;
	glow: number; // glow strength 0..1 (0 = off)
	glowSpread: number; // glow blur radius, cells
}

export const DEFAULTS: Params = {
	cell: 22,
	radius: 0.22,
	gap: 0.08,
	rows: 8,
	bar: 8,
	xh: 5,
	bowl: 4,
	caret: 3,
	leg: 3,
	nDrop: 2,
	space: 1,
	density: 1,
	margin: 1,
	nudgeX: 0,
	nudgeY: 0,
	nStyle: 0,
	glow: 0,
	glowSpread: 0.5
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
	{ key: 'space', label: 'letter spacing', min: 0, max: 3, step: 1 },
	{ key: 'nStyle', label: 'n style (0 arch, 1 mirror)', min: 0, max: 1, step: 1 },
	{ key: 'density', label: 'density (pixels per cell side)', min: 1, max: 4, step: 1 },
	{ key: 'margin', label: 'margin (cells)', min: 0, max: 4, step: 0.25 },
	{ key: 'nudgeX', label: 'nudge x (cells)', min: -4, max: 4, step: 0.25 },
	{ key: 'nudgeY', label: 'nudge y (cells)', min: -4, max: 4, step: 0.25 },
	{ key: 'glow', label: 'glow strength', min: 0, max: 1, step: 0.05 },
	{ key: 'glowSpread', label: 'glow spread', min: 0.1, max: 2, step: 0.05 }
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

/** Per-frame pixel overrides: 'x,y' -> true (forced on) or false (forced off). Keyed by frameKey(). */
export type Overrides = Record<string, Record<string, boolean>>;
export function frameKey(pose: Pose, density = 1): string {
	const caret = pose.eyeL === 'caret' && pose.eyeR === 'caret';
	const k =
		caret && pose.t === 0
			? 'rest'
			: caret && pose.t === 1
				? 'awake'
				: `${pose.t}:${pose.eyeL}:${pose.eyeR}`;
	// flips address the fine grid, so each density keeps its own set
	return density > 1 ? `${k}@${density}` : k;
}

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
	return {
		...p,
		rows,
		bar,
		xh,
		caret,
		bowl: Math.round(clamp(p.bowl, 3, 7)),
		density: Math.round(clamp(p.density, 1, 4))
	};
}

export function layout(params: Params, pose: Pose = REST, ov?: Overrides): Layout {
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

	// n, two builds. Awake, its top drops (feet stay on the baseline).
	const rise = (p.caret + 1) / 2;
	const mid = (p.caret - 1) / 2;
	if (p.nStyle === 1) {
		// mirror: the r and its reflection sharing one foot column.
		const nw = 2 * p.caret - 1;
		const nTop = top + Math.round(Math.min(p.nDrop, p.xh - rise) * pose.t);
		for (const c0 of [x0, x0 + p.caret - 1]) {
			for (let k = 0; k < rise; k++) {
				put(c0 + mid - k, nTop + k);
				if (k > 0) put(c0 + mid + k, nTop + k);
			}
		}
		for (let y = nTop + rise; y <= base; y++) {
			put(x0, y);
			put(x0 + nw - 1, y);
		}
		x0 += nw + p.space;
	} else {
		// arch: two legs and a lid; the lid's corner cells are cut so the shoulders round.
		const nTop = top + Math.round(Math.min(p.nDrop, p.xh - 2) * pose.t);
		for (let y = nTop + 1; y <= base; y++) {
			put(x0, y);
			put(x0 + 3, y);
		}
		put(x0 + 1, nTop);
		put(x0 + 2, nTop);
		x0 += 4 + p.space;
	}

	r(x0, pose.eyeR);
	x0 += p.caret + p.space;

	bowl(x0, true);
	x0 += p.bowl;

	// a cell can be emitted twice (n corners, ^ apex); keep the first.
	const seen = new Set<string>();
	const uniq0 = cells.filter((c) => {
		const k = `${c.x},${c.y}`;
		if (seen.has(k)) return false;
		seen.add(k);
		return true;
	});
	// density N: every cell becomes an N x N block of pixels
	const n = p.density;
	let uniq: Cell[] = uniq0;
	if (n > 1) {
		uniq = [];
		for (const c of uniq0)
			for (let i = 0; i < n; i++)
				for (let j = 0; j < n; j++) uniq.push({ x: c.x * n + i, y: c.y * n + j, eye: c.eye });
	}
	const cols = x0 * n;
	const rowsF = p.rows * n;
	const mine = ov?.[frameKey(pose, n)];
	if (mine) {
		uniq = uniq.filter((c) => mine[`${c.x},${c.y}`] !== false);
		const have = new Set(uniq.map((c) => `${c.x},${c.y}`));
		for (const [k, on] of Object.entries(mine)) {
			const [x, y] = k.split(',').map(Number);
			if (on && x < cols && y < rowsF && !have.has(k)) uniq.push({ x, y, eye: false });
		}
	}
	return { cols, rows: rowsF, cells: uniq };
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
	scan: boolean;
	grain: boolean;
	jitter: boolean;
}
export const NO_FX: Effects = { scan: false, grain: false, jitter: false };

export interface Render {
	pose: Pose;
	palette: Pick<Palette, 'fg' | 'bg' | 'accent'>;
	fx: Effects;
	/** Frame: 'fit' hugs the mark; 'square' and 'wide' put it on a tile with padding. */
	frame?: 'fit' | 'square' | 'wide';
	/** 'circle' clips the background to a circle (the avatar). */
	shape?: 'rect' | 'circle';
	/** Rendered pixel height; width follows. Defaults to pixel size x the canvas height in pixels. */
	height?: number;
	/** Unique id prefix when several SVGs share a page. */
	id?: string;
	/** Glow colour; empty = the ink colour. */
	glowColor?: string;
	/** Per-frame pixel overrides. */
	ov?: Overrides;
	/** Draw the background (false = transparent). */
	background?: boolean;
}

const hash = (x: number, y: number) => {
	let h = (x * 374761393 + y * 668265263) | 0;
	h = Math.imul(h ^ (h >>> 13), 1274126177);
	return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
};

/** The canvas a mark is drawn on, in fine pixels (one pixel = 1 unit), and where the mark sits on it. */
export interface Canvas {
	w: number;
	h: number;
	ox: number;
	oy: number;
	lay: Layout;
}

/**
 * Every box fits the whole mark plus its margin (and the glow's reach) and
 * centres it, never crops: 'fit' hugs it, 'square' is a tile, 'wide' a 3:1
 * banner, and a 'circle' shape fits the mark inside the inscribed square.
 */
export function canvas(
	params: Params,
	pose: Pose,
	ov?: Overrides,
	frame: 'fit' | 'square' | 'wide' = 'fit',
	shape: 'rect' | 'circle' = 'rect'
): Canvas {
	const p = fit(params);
	const lay = layout(p, pose, ov);
	const n = p.density;
	const room = (p.margin + (p.glow > 0 ? 3 * p.glowSpread : 0)) * n;
	const mw = lay.cols + 2 * room;
	const mh = lay.rows + 2 * room;
	let w = mw;
	let h = mh;
	if (frame === 'square') w = h = Math.max(mw, mh) * (shape === 'circle' ? Math.SQRT2 : 1);
	else if (frame === 'wide') {
		h = Math.max(mh, mw / 3);
		w = 3 * h;
	}
	return {
		w,
		h,
		ox: (w - lay.cols) / 2 + p.nudgeX * n,
		oy: (h - lay.rows) / 2 + p.nudgeY * n,
		lay
	};
}

/** The pixel under a point given as fractions (0..1) of the rendered box, or null outside the mark's grid. */
export function cellAt(
	params: Params,
	pose: Pose,
	ov: Overrides | undefined,
	fx: number,
	fy: number,
	frame: 'fit' | 'square' | 'wide' = 'fit'
): { x: number; y: number } | null {
	const c = canvas(params, pose, ov, frame);
	const x = Math.floor(fx * c.w - c.ox);
	const y = Math.floor(fy * c.h - c.oy);
	return x < 0 || y < 0 || x >= c.lay.cols || y >= c.lay.rows ? null : { x, y };
}

export function toSvg(params: Params, r: Render): string {
	const p = fit(params);
	const frame = r.frame ?? 'fit';
	const cv = canvas(p, r.pose, r.ov, frame, r.shape);
	const id = r.id ?? 'bl';
	// Draw in output pixels, svg box = viewBox, so no browser has a scale to get wrong.
	const H = Math.round(r.height ?? p.cell * cv.h);
	const u = H / cv.h;
	const W = Math.round(cv.w * u);
	const inset = (p.gap / 2) * u;
	const size = (1 - p.gap) * u;
	const rx = p.radius * size;
	const bgRect =
		r.background === false
			? ''
			: r.shape === 'circle'
				? `<circle cx="${W / 2}" cy="${H / 2}" r="${Math.min(W, H) / 2}" fill="${r.palette.bg}"/>`
				: `<rect width="${W}" height="${H}" fill="${r.palette.bg}"/>`;
	const rects = cv.lay.cells
		.map((c) => {
			const j = r.fx.jitter ? 0.55 + 0.45 * hash(c.x, c.y) : 1;
			const fill = c.eye ? r.palette.accent : r.palette.fg;
			const op = j < 1 ? ` fill-opacity="${j.toFixed(2)}"` : '';
			return `<rect x="${((cv.ox + c.x) * u + inset).toFixed(2)}" y="${((cv.oy + c.y) * u + inset).toFixed(2)}" width="${size.toFixed(2)}" height="${size.toFixed(2)}" rx="${rx.toFixed(2)}" fill="${fill}"${op}/>`;
		})
		.join('');
	const defs: string[] = [];
	if (p.glow > 0) {
		// spread is in cells, so it grows with the grid and not with the pixel size
		const sd = (p.glowSpread * p.density * u).toFixed(2);
		defs.push(
			`<filter id="${id}g" filterUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><feGaussianBlur in="SourceAlpha" stdDeviation="${sd}"/><feComponentTransfer result="a"><feFuncA type="linear" slope="${(p.glow * 4).toFixed(2)}"/></feComponentTransfer><feFlood flood-color="${r.glowColor || r.palette.fg}" result="c"/><feComposite in="c" in2="a" operator="in"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>`
		);
	}
	if (r.fx.grain)
		defs.push(
			`<filter id="${id}n" filterUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7"/><feColorMatrix values="0 0 0 0 0.5  0 0 0 0 0.5  0 0 0 0 0.5  0 0 0 0.35 0"/></filter>`
		);
	if (r.fx.scan) {
		const per = Math.max(2, u / 3);
		defs.push(
			`<pattern id="${id}s" width="${W}" height="${per.toFixed(2)}" patternUnits="userSpaceOnUse"><rect width="${W}" height="${(per / 2).toFixed(2)}" fill="#000" fill-opacity="0.28"/></pattern>`
		);
	}
	if (r.shape === 'circle')
		defs.push(
			`<clipPath id="${id}c"><circle cx="${W / 2}" cy="${H / 2}" r="${Math.min(W, H) / 2}"/></clipPath>`
		);
	const body =
		`<g${p.glow > 0 ? ` filter="url(#${id}g)"` : ''}>${rects}</g>` +
		(r.fx.scan ? `<rect width="${W}" height="${H}" fill="url(#${id}s)"/>` : '') +
		(r.fx.grain ? `<rect width="${W}" height="${H}" filter="url(#${id}n)"/>` : '');
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="width:${W}px;height:${H}px;max-width:none;display:block" shape-rendering="geometricPrecision">` +
		(defs.length ? `<defs>${defs.join('')}</defs>` : '') +
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
	glowColor: string; // '' = ink
	ov: Overrides;
	fx: Effects;
}

export const FX_KEYS = ['scan', 'grain', 'jitter'] as const;

export function toQuery(s: Settings, base: Settings): string {
	const q = new URLSearchParams();
	for (const k of Object.keys(DEFAULTS) as (keyof Params)[])
		if (s.p[k] !== base.p[k]) q.set(k, String(s.p[k]));
	if (s.pose.t !== base.pose.t) q.set('t', String(s.pose.t));
	if (s.pose.eyeL !== base.pose.eyeL) q.set('eyeL', s.pose.eyeL);
	if (s.pose.eyeR !== base.pose.eyeR) q.set('eyeR', s.pose.eyeR);
	for (const k of ['fg', 'bg', 'accent'] as const)
		if (s[k] !== base[k]) q.set(k, s[k].replace('#', ''));
	if (s.glowColor !== base.glowColor) q.set('glowColor', s.glowColor.replace('#', ''));
	const ov = Object.entries(s.ov)
		.filter(([, m]) => Object.keys(m).length)
		.map(
			([f, m]) =>
				`${f}~${Object.entries(m)
					.map(([k, on]) => `${k.replace(',', '.')}${on ? '+' : '-'}`)
					.join('_')}`
		)
		.join('!');
	if (ov) q.set('ov', ov);
	const fx = FX_KEYS.filter((k) => s.fx[k]).join(',');
	if (fx) q.set('fx', fx);
	return q.toString();
}

export function fromQuery(q: URLSearchParams, base: Settings): Settings {
	const s: Settings = {
		...base,
		p: { ...base.p },
		pose: { ...base.pose },
		ov: {},
		fx: { ...NO_FX }
	};
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
	const gc = q.get('glowColor');
	if (gc && /^[0-9a-f]{6}$/i.test(gc)) s.glowColor = `#${gc}`;
	for (const part of (q.get('ov') ?? '').split('!')) {
		const [f, cellsStr] = part.split('~');
		if (!f || !cellsStr) continue;
		s.ov[f] = {};
		for (const c of cellsStr.split('_')) {
			const m = /^(\d+)\.(\d+)([+-])$/.exec(c);
			if (m) s.ov[f][`${m[1]},${m[2]}`] = m[3] === '+';
		}
	}
	for (const k of (q.get('fx') ?? '').split(','))
		if ((FX_KEYS as readonly string[]).includes(k)) s.fx[k as keyof Effects] = true;
	return s;
}
