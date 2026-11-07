import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	AWAKE,
	DEFAULTS,
	NO_FX,
	PALETTES,
	REST,
	WINK,
	fromQuery,
	layout,
	toQuery,
	toSvg
} from './brnrdLogoGeometry.ts';
import type { Settings } from './brnrdLogoGeometry.ts';

const key = (c: { x: number; y: number }) => `${c.x},${c.y}`;

test('every wink frame is as wide as the resting mark, so it never jitters', () => {
	const w = layout(DEFAULTS, REST).cols;
	for (const f of WINK) assert.equal(layout(DEFAULTS, f.pose).cols, w, f.name);
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
		fx: { ...NO_FX }
	};
	assert.equal(toQuery(base, base), '');
	const s: Settings = {
		...base,
		p: { ...DEFAULTS, gap: 0.2, rows: 12 },
		pose: { t: 0.5, eyeL: 'dash', eyeR: 'caret' },
		fg: '#112233',
		fx: { ...NO_FX, glow: true }
	};
	const back = fromQuery(new URLSearchParams(toQuery(s, base)), base);
	assert.deepEqual(back, s);
});

test('svg renders for every palette and effect, with the cell count it was given', () => {
	const fx = { glow: true, scan: true, grain: true, jitter: true };
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
