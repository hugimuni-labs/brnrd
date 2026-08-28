import assert from 'node:assert/strict';
import test from 'node:test';

import {
	HUGIMUNI_DEFAULTS,
	HUGIMUNI_WORDMARK,
	hugimuniConstantBlock,
	hugimuniFlatSvg,
	hugimuniHComponents,
	hugimuniLockupSvg,
	hugimuniMComponents,
	hugimuniScreenSvg,
	hugimuniWordmarkMetrics,
	hugimuniWordmarkSvg
} from './hugimuniBrandGeometry.ts';

test('canonical H and M components keep the authored displaced geometry', () => {
	assert.deepEqual(hugimuniHComponents(HUGIMUNI_DEFAULTS), [
		[145, 156, 145, 356, 40],
		[353, 156, 353, 356, 40],
		[132, 276, 380, 276, 28]
	]);
	assert.deepEqual(hugimuniMComponents(HUGIMUNI_DEFAULTS), [
		[159, 156, 159, 356, 40],
		[367, 156, 367, 356, 40],
		[132, 156, 276, 356, 28],
		[380, 156, 236, 356, 28]
	]);
});

test('flat register encodes the three-region identity with no filters', () => {
	const out = hugimuniFlatSvg(HUGIMUNI_DEFAULTS, 'test-flat');
	assert.match(out, /stroke="#ff9a1f"/);
	assert.match(out, /stroke="#69c7df"/);
	assert.match(out, /stroke="#f0e3cf"/);
	assert.match(out, /mask="url\(#test-flat-h\)"/);
	assert.doesNotMatch(out, /<filter/);
});

test('screen register is the flat identity plus atmosphere', () => {
	const out = hugimuniScreenSvg(HUGIMUNI_DEFAULTS, 'test-screen');
	assert.match(out, /test-screen-flat-art/);
	assert.match(out, /<feGaussianBlur stdDeviation="7"/);
	assert.match(out, /opacity="0\.42"/);
	assert.match(out, /fractalNoise/);
});

test('wordmark reuses authored H/M geometry but keeps the initials separate', () => {
	const out = hugimuniWordmarkSvg(HUGIMUNI_DEFAULTS, 'test-wordmark');
	const metrics = hugimuniWordmarkMetrics(HUGIMUNI_DEFAULTS);
	assert.equal(HUGIMUNI_WORDMARK, 'HugiMuni');
	assert.equal(Number(metrics.scale.toFixed(2)), 0.16);
	assert.ok(metrics.ugiX < metrics.mX);
	assert.match(out, /id="test-wordmark-h"/);
	assert.match(out, /id="test-wordmark-m"/);
	assert.match(out, /fill="#ff9a1f"[^>]*>ugi<\/text>/);
	assert.match(out, /fill="#69c7df"[^>]*>uni<\/text>/);
	assert.doesNotMatch(out, />HugiMuni<\/text>/);
	assert.doesNotMatch(out, /mask="url\(/);
});

test('lockup is one visual word below the mark: Hugi amber, Muni sky', () => {
	const out = hugimuniLockupSvg(HUGIMUNI_DEFAULTS, 'flat', 'test-lockup');
	assert.match(out, /test-lockup-wordmark-h/);
	assert.match(out, /test-lockup-wordmark-m/);
	assert.match(out, /fill="#ff9a1f"[^>]*>ugi<\/text>/);
	assert.match(out, /fill="#69c7df"[^>]*>uni<\/text>/);
	assert.doesNotMatch(out, />UGI</);
	assert.doesNotMatch(out, />UNI</);
});

test('constant block maps live bench values back to build.py names', () => {
	const out = hugimuniConstantBlock(HUGIMUNI_DEFAULTS);
	assert.match(out, /^LEFT, RIGHT = 152, 360$/m);
	assert.match(out, /^TAIL = 20$/m);
	assert.match(out, /^AMBER = "#ff9a1f"$/m);
	assert.match(out, /^BLOOM_OPACITY = 0\.42$/m);
});
