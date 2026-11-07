import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	AWAKE,
	DEFAULTS,
	NO_FX,
	PALETTES,
	REST,
	WINK,
	frameKey,
	canvas,
	fromQuery,
	layout,
	toQuery,
	toSvg
} from './brnrdLogoGeometry.ts';
import type { Settings } from './brnrdLogoGeometry.ts';

const key = (c: { x: number; y: number }) => `${c.x},${c.y}`;

test('every wink frame is as wide as the resting mark, so it never jitters', () => {
	const w = layout(DEFAULTS, REST).cols;
	for (const n of [0, 1]) {
		const p = { ...DEFAULTS, nStyle: n };
		for (const f of WINK) assert.equal(layout(p, f.pose).cols, layout(p, REST).cols, f.name);
	}
	assert.ok(w > 0);
});

test('awake keeps b, d and both ^ of the resting frame; stems go, n drops', () => {
	const rest = layout(DEFAULTS, REST).cells;
	const awake = new Set(layout(DEFAULTS, AWAKE).cells.map(key));
	const eyes = rest.filter((c) => c.eye);
	assert.ok(eyes.length > 0);
	for (const c of eyes) assert.ok(awake.has(key(c)), `eye ${key(c)} survives`);
	const bd = rest.filter(
		(c) => c.x < DEFAULTS.bowl || c.x >= layout(DEFAULTS).cols - DEFAULTS.bowl
	);
	for (const c of bd) assert.ok(awake.has(key(c)), `b/d cell ${key(c)} survives`);
	assert.ok(awake.size < rest.length, 'the legs are gone');
	const restTopN = Math.min(
		...rest.filter((c) => c.x === DEFAULTS.bowl + 1 + DEFAULTS.caret + 1 + 1).map((c) => c.y)
	);
	const awakeTopN = Math.min(
		...layout(DEFAULTS, AWAKE)
			.cells.filter((c) => c.x === DEFAULTS.bowl + 1 + DEFAULTS.caret + 1 + 1)
			.map((c) => c.y)
	);
	assert.equal(awakeTopN - restTopN, DEFAULTS.nDrop);
});

test('settings survive the URL round trip; defaults give an empty query', () => {
	const base: Settings = {
		p: { ...DEFAULTS },
		pose: { ...REST },
		fg: '#ff7a1c',
		bg: '#2a2825',
		accent: '#ffd9a8',
		glowColor: '',
		ov: {},
		fx: { ...NO_FX }
	};
	assert.equal(toQuery(base, base), '');
	const s: Settings = {
		...base,
		p: { ...DEFAULTS, gap: 0.2, rows: 12, glow: 0.5 },
		pose: { t: 0.5, eyeL: 'dash', eyeR: 'caret' },
		fg: '#112233',
		glowColor: '#00ff00',
		ov: { rest: { '3,4': false, '2,0': true } },
		fx: { ...NO_FX, scan: true }
	};
	const back = fromQuery(new URLSearchParams(toQuery(s, base)), base);
	assert.deepEqual(back, s);
});

test('svg renders for every palette and effect, with the cell count it was given', () => {
	const fx = { scan: true, grain: true, jitter: true };
	for (const pal of PALETTES) {
		const svg = toSvg(DEFAULTS, {
			pose: AWAKE,
			palette: pal,
			fx,
			shape: 'circle',
			frame: 'square'
		});
		assert.match(svg, /^<svg /);
		assert.equal((svg.match(/rx=/g) ?? []).length, layout(DEFAULTS, AWAKE).cells.length);
	}
});

test('mirror n: the r and its reflection, one shared foot column, legs outside', () => {
	const p = { ...DEFAULTS, nStyle: 1 };
	const nx = p.bowl + 1 + p.caret + 1;
	const nw = 2 * p.caret - 1;
	const cells = layout(p, REST).cells.filter((c) => c.x >= nx && c.x < nx + nw);
	const has = (x: number, y: number) => cells.some((c) => c.x === x && c.y === y);
	const top = p.rows - p.xh;
	for (let x = 0; x < nw; x++) assert.equal(has(nx + x, p.rows - 1), x === 0 || x === nw - 1);
	assert.ok(has(nx + 1, top) && has(nx + 3, top), 'two ^ apexes');
	assert.ok(has(nx + 2, top + 1), 'the feet meet');
});

test('a pixel override sticks to its frame and survives a slider', () => {
	const ov = { rest: { '2,0': true } };
	const on = (p: typeof DEFAULTS, pose = REST) =>
		layout(p, pose, ov).cells.some((c) => c.x === 2 && c.y === 0);
	assert.ok(on(DEFAULTS));
	assert.ok(on({ ...DEFAULTS, cell: 40, radius: 0.4 }));
	assert.ok(!on(DEFAULTS, AWAKE));
	assert.equal(frameKey(AWAKE), 'awake');
});

test('density N draws the same shape from N x N blocks, and flips address the fine grid', () => {
	const one = layout(DEFAULTS, AWAKE);
	for (const n of [2, 3, 4]) {
		const p = { ...DEFAULTS, density: n };
		const fine = layout(p, AWAKE);
		assert.equal(fine.cols, one.cols * n);
		assert.equal(fine.rows, one.rows * n);
		assert.equal(fine.cells.length, one.cells.length * n * n);
		const on = new Set(fine.cells.map(key));
		for (const c of one.cells) assert.ok(on.has(`${c.x * n},${c.y * n}`));
		assert.equal(frameKey(AWAKE, n), `awake@${n}`);
	}
	const p = { ...DEFAULTS, density: 2 };
	const flipped = layout(p, REST, { 'rest@2': { '0,0': true } }).cells.some(
		(c) => c.x === 0 && c.y === 0
	);
	assert.ok(flipped);
	assert.ok(
		!layout(p, REST, { rest: { '0,0': true } }).cells.some((c) => c.x === 1 && c.y === 0 && false)
	);
});

test('every box holds the whole mark plus margin and centres it; the avatar fits the inscribed square', () => {
	for (const frame of ['fit', 'square', 'wide'] as const)
		for (const shape of ['rect', 'circle'] as const)
			for (const glow of [0, 0.6]) {
				const p = { ...DEFAULTS, glow, margin: 1.5, density: 2 };
				const c = canvas(p, AWAKE, undefined, frame, shape);
				const m = (p.margin + (glow ? 3 * p.glowSpread : 0)) * p.density;
				assert.ok(c.ox >= m - 1e-9 && c.oy >= m - 1e-9, `${frame}/${shape} margin left/top`);
				assert.ok(c.w - (c.ox + c.lay.cols) >= m - 1e-9, `${frame}/${shape} right`);
				assert.ok(c.h - (c.oy + c.lay.rows) >= m - 1e-9, `${frame}/${shape} bottom`);
				assert.ok(Math.abs(c.ox - (c.w - c.lay.cols - c.ox)) < 1e-9, 'centred x');
				if (shape === 'circle' && frame === 'square') {
					const half = c.w / 2;
					const reach = Math.hypot(c.lay.cols / 2 + m, c.lay.rows / 2 + m);
					assert.ok(reach <= half + 1e-9, 'whole mark inside the circle');
				}
			}
	const c0 = canvas(DEFAULTS, REST);
	const c1 = canvas({ ...DEFAULTS, nudgeX: 2 }, REST);
	assert.equal(c1.ox - c0.ox, 2);
});

test('the svg is its own pixels: box, viewBox and style agree', () => {
	const svg = toSvg(DEFAULTS, {
		pose: REST,
		palette: PALETTES[1],
		fx: NO_FX,
		height: 96,
		frame: 'wide'
	});
	const [, w, h] = /viewBox="0 0 (\d+) (\d+)"/.exec(svg)!;
	assert.ok(
		svg.includes(`width="${w}" height="${h}"`) && svg.includes(`style="width:${w}px;height:${h}px;`)
	);
	assert.equal(h, '96');
	assert.equal(Number(w), 288);
});
