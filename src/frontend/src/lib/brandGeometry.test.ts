import assert from 'node:assert/strict';
import test from 'node:test';

import {
	BRNRD_DEFAULTS,
	FACES,
	HUGIMUNI_DEFAULTS,
	brnrdConstantBlock,
	glyph,
	hugimuniBarH,
	hugimuniConstantBlock,
	hugimuniStems,
	hugimuniSvg,
	hugimuniVeeM,
	letterN,
	letterR,
	restingBody,
	skeletonBody,
	slots,
	stavePath
} from './brandGeometry.ts';
import type { BrnrdConstants } from './brandGeometry.ts';

// Expected values below are lifted from running the actual Python
// (`media/brand/build.py` / `media/brand/hugimuni/build.py`, PR #1488's
// branch) against the same default constants, with one adjustment: Python's
// `AXIS = BOARD / 2` is a float, so every coordinate derived from it prints
// with a trailing `.0` (`M 72.0 92 ...`) in the Python output; JS has one
// numeric type, so the identical arithmetic here prints `M 72 92 ...`. Every
// expected string below is the Python output with that cosmetic `.0` diff
// removed — numerically identical geometry, see brandGeometry.ts's module
// doc for why the difference exists and why it doesn't affect the
// copy-constants pasteback.

test('slots: five positions centred on the 512 board, 80 apart', () => {
	assert.deepEqual(slots(80), [96, 176, 256, 336, 416]);
});

test('stavePath: left stave (b), no crown', () => {
	const s = slots(BRNRD_DEFAULTS.SLOT);
	const out = stavePath(s[0] - BRNRD_DEFAULTS.STAVE_INSET, -1, BRNRD_DEFAULTS);
	assert.equal(
		out,
		'\n    <path d="M 72 92 V 420"/>\n    <path d="M 48 196 H 96"/>\n    <path d="M 48 236 H 96"/>\n    <path d="M 72 308 a 62 56 0 0 1 0 112"/>'
	);
});

test('stavePath: right stave (d), no crown', () => {
	const s = slots(BRNRD_DEFAULTS.SLOT);
	const out = stavePath(s[4] + BRNRD_DEFAULTS.STAVE_INSET, 1, BRNRD_DEFAULTS);
	assert.equal(
		out,
		'\n    <path d="M 440 92 V 420"/>\n    <path d="M 416 196 H 464"/>\n    <path d="M 416 236 H 464"/>\n    <path d="M 440 308 a 62 56 0 0 0 0 112"/>'
	);
});

test('stavePath: fork crown adds the trident', () => {
	const c: BrnrdConstants = { ...BRNRD_DEFAULTS, CROWN: 'fork' };
	const out = stavePath(72, -1, c);
	assert.match(out, /<path d="M 40 132 H 104"\/>/);
	assert.match(out, /<path d="M 40 132 V 100"\/>/);
	assert.match(out, /<path d="M 104 132 V 100"\/>/);
});

test('stavePath: branch crown adds the two rising arms', () => {
	const c: BrnrdConstants = { ...BRNRD_DEFAULTS, CROWN: 'branch' };
	const out = stavePath(72, -1, c);
	assert.match(out, /<path d="M 72 146 L 34 96"\/>/);
	assert.match(out, /<path d="M 72 146 L 110 96"\/>/);
});

test('glyph: every kind at x=200 with default eye/mouth constants', () => {
	assert.equal(
		glyph('dot', 200, BRNRD_DEFAULTS),
		'<circle cx="200" cy="322" r="15" fill="url(#molten)" stroke="none"/>'
	);
	assert.equal(
		glyph('lown', 200, BRNRD_DEFAULTS),
		'<path d="M 174 378 V 404"/><path d="M 226 378 V 404"/><path d="M 174 378 L 200 364 L 226 378"/>'
	);
	assert.equal(glyph('peak', 200, BRNRD_DEFAULTS), '<path d="M 174 346 L 200 322 L 226 346"/>');
	assert.equal(
		glyph('ring', 200, BRNRD_DEFAULTS),
		'<path d="M 200 300 a 22 22 0 1 0 0.01 0" fill="none"/>'
	);
	assert.equal(glyph('dash', 200, BRNRD_DEFAULTS), '<path d="M 180 322 H 220"/>');
	assert.equal(glyph('bar', 200, BRNRD_DEFAULTS), '<path d="M 152 390 H 248"/>');
	assert.equal(
		glyph('grit', 200, BRNRD_DEFAULTS),
		'<path d="M 152 390 H 248"/><path d="M 152 362 H 248"/>'
	);
});

test('FACES: all six states from the Python dict are present', () => {
	assert.deepEqual(
		Object.keys(FACES).sort(),
		['flat', 'grip', 'kawaii', 'rest', 'up', 'wide'].sort()
	);
	assert.deepEqual(FACES.rest, ['dot', 'bar', 'dot']);
	assert.deepEqual(FACES.grip, ['dot', 'grit', 'dot']);
});

test('skeletonBody: rest face, full body matches the Python reference', () => {
	const out = skeletonBody('rest', BRNRD_DEFAULTS);
	assert.equal(
		out,
		'\n    <path d="M 72 92 V 420"/>\n    <path d="M 48 196 H 96"/>\n    <path d="M 48 236 H 96"/>\n    <path d="M 72 308 a 62 56 0 0 1 0 112"/>\n    <path d="M 440 92 V 420"/>\n    <path d="M 416 196 H 464"/>\n    <path d="M 416 236 H 464"/>\n    <path d="M 440 308 a 62 56 0 0 0 0 112"/><circle cx="176" cy="322" r="15" fill="url(#molten)" stroke="none"/><path d="M 208 390 H 304"/><circle cx="336" cy="322" r="15" fill="url(#molten)" stroke="none"/>'
	);
});

test('letterR / letterN / restingBody match the Python reference', () => {
	assert.equal(
		letterR(200, false, BRNRD_DEFAULTS),
		'\n    <path d="M 188 308 V 420"/>\n    <path d="M 188 312 L 226 288"/>'
	);
	assert.equal(
		letterR(200, true, BRNRD_DEFAULTS),
		'\n    <path d="M 212 308 V 420"/>\n    <path d="M 212 312 L 174 288"/>'
	);
	assert.equal(
		letterN(200, BRNRD_DEFAULTS),
		'\n    <path d="M 175 308 V 420"/>\n    <path d="M 225 322 V 420"/>\n    <path d="M 175 322 L 200 308 L 225 322"/>'
	);
	const resting = restingBody(BRNRD_DEFAULTS);
	assert.match(resting, /<path d="M 164 308 V 420"\/>/); // slot 1 stem
	assert.match(resting, /<path d="M 348 312 L 310 288"\/>/); // slot 3 mirrored r
	assert.equal(resting.length, 522); // full-string length pin against drift
});

test('brnrdConstantBlock: emits pasteable Python assignments, XTOP flagged', () => {
	const block = brnrdConstantBlock(BRNRD_DEFAULTS);
	assert.match(block, /^SLOT = 80$/m);
	assert.match(block, /^CROWN = "none"$/m);
	assert.match(block, /XTOP = 308/);
	assert.match(block, /XTOP = BOWL_TOP by assignment/);
});

test('hugimuni: STEMS / BAR_H / vee_m match the Python reference', () => {
	assert.equal(
		hugimuniStems(HUGIMUNI_DEFAULTS),
		'<path d="M 152 156 V 356"/><path d="M 360 156 V 356"/>'
	);
	assert.equal(hugimuniBarH(HUGIMUNI_DEFAULTS), '<path d="M 132 276 H 380"/>');
	assert.equal(
		hugimuniVeeM(HUGIMUNI_DEFAULTS),
		'<path d="M 132 156 L 276 356"/><path d="M 380 156 L 236 356"/>'
	);
});

test('hugimuniSvg: emissive render — transparent ground, three passes, grain on the strokes', () => {
	const out = hugimuniSvg(HUGIMUNI_DEFAULTS, 'amber-sky');
	// the rounded ink board is gone: the mark is transparent and carries its
	// own light (2026-08-28, from the maintainer's generated reference)
	assert.doesNotMatch(out, /rx="112"/);
	assert.doesNotMatch(out, /fill="#080b09"/);
	// body pass: full-width palette strokes; core stems keep the tunable
	// intersection colour
	assert.match(out, /stroke-width="40"[^>]+stroke="#ff9a1f"/);
	assert.match(out, /stroke-width="40"[^>]+stroke="#69c7df"/);
	assert.match(out, /stroke-width="26"[^>]+stroke="#eadfca"/);
	// bloom halo + white-hot cores (~34% width) around every stroke
	assert.match(out, /filter="url\(#hm-bloom\)"/);
	assert.match(out, /stroke-width="8"[^>]+stroke="#fff6e4"/); // round(40*.2)
	assert.match(out, /stroke-width="6"[^>]+stroke="#fff6e4"/); // round(28*.2)
	// grain is masked to the strokes and driven by GRAIN (58 → .348/.319)
	assert.match(out, /<mask id="hm-strokes">/);
	assert.match(out, /mask="url\(#hm-strokes\)"/);
	assert.match(out, /filter="url\(#hm-grain\)" opacity="0\.348"/);
	assert.match(out, /fill="url\(#hm-scanlines\)" opacity="0\.319"/);
});

test('hugimuniConstantBlock: emits pasteable assignments, TAIL flagged as function-local', () => {
	const block = hugimuniConstantBlock(HUGIMUNI_DEFAULTS);
	assert.match(block, /^LEFT, RIGHT = 152, 360$/m);
	assert.match(block, /^STEM_STROKE = 40$/m);
	assert.match(block, /^GHOST = 7$/m);
	assert.match(block, /^GRAIN = 58$/m);
	assert.match(block, /^INTERSECTION = "#eadfca"$/m);
	assert.match(block, /TAIL lives inside vee_m/);
	assert.match(block, /TAIL = 20.*carries this value in build\.py\.$/m);
});
