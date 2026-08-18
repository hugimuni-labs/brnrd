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
		'<path d="M 172 156 V 356"/><path d="M 340 156 V 356"/>'
	);
	assert.equal(hugimuniBarH(HUGIMUNI_DEFAULTS), '<path d="M 138 268 H 374"/>');
	assert.equal(
		hugimuniVeeM(HUGIMUNI_DEFAULTS),
		'<path d="M 122 156 L 286 366"/><path d="M 390 156 L 226 366"/>'
	);
});

test('hugimuniSvg: full amber-sky render matches the Python reference', () => {
	const out = hugimuniSvg(HUGIMUNI_DEFAULTS, 'amber-sky');
	assert.equal(
		out,
		'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">\n' +
			'  <title>hugimuni — H and M on shared stems (amber-sky)</title>\n' +
			'  <rect width="512" height="512" rx="112" fill="#0c0906"/>\n' +
			'  <g style="mix-blend-mode:screen">\n' +
			'    <g fill="none" stroke-width="30" stroke-linecap="round" stroke-linejoin="round" style="mix-blend-mode:screen" stroke="#ff9a1f" transform="translate(-5,0)"><path d="M 172 156 V 356"/><path d="M 340 156 V 356"/></g>\n' +
			'    <g fill="none" stroke-width="30" stroke-linecap="round" stroke-linejoin="round" style="mix-blend-mode:screen" stroke="#8fb6cc" transform="translate(5,0)"><path d="M 172 156 V 356"/><path d="M 340 156 V 356"/></g>\n' +
			'    <g fill="none" stroke-width="30" stroke-linecap="round" stroke-linejoin="round" style="mix-blend-mode:screen" stroke="#ff9a1f"><path d="M 138 268 H 374"/></g>\n' +
			'    <g fill="none" stroke-width="30" stroke-linecap="round" stroke-linejoin="round" style="mix-blend-mode:screen" stroke="#8fb6cc"><path d="M 122 156 L 286 366"/><path d="M 390 156 L 226 366"/></g>\n' +
			'  </g>\n' +
			'</svg>\n'
	);
});

test('hugimuniConstantBlock: emits pasteable assignments, TAIL flagged as function-local', () => {
	const block = hugimuniConstantBlock(HUGIMUNI_DEFAULTS);
	assert.match(block, /^LEFT, RIGHT = 172, 340$/m);
	assert.match(block, /^GHOST = 5$/m);
	assert.match(block, /TAIL lives inside vee_m/);
	assert.match(block, /TAIL = 30 to carry this value over\.$/m);
});
