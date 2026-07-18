import assert from 'node:assert/strict';
import test from 'node:test';

import {
	STATUS_AMPLE,
	STATUS_BURNING,
	STATUS_COOLING,
	STATUS_CRITICAL,
	STATUS_LOW,
	STATUS_SPENT,
	THERMAL_SCALE,
	glowFor,
	urgencyForLevel
} from './statusPalette.ts';
import {
	TYPE_REVEAL_GLYPHS,
	shouldRestartReveal,
	typeRevealDuration,
	typeRevealProgress
} from './transitions.ts';

function luminance(hex: string): number {
	const channels = hex
		.slice(1)
		.match(/.{2}/gu)!
		.map((pair) => Number.parseInt(pair, 16) / 255)
		.map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
	return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a: string, b: string): number {
	const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
	return (lighter + 0.05) / (darker + 0.05);
}

test('thermal tiers preserve shipped colors and deprecated aliases', () => {
	assert.equal(STATUS_BURNING, '#e8b34a');
	assert.equal(STATUS_COOLING, '#a8cbdb');
	assert.equal(STATUS_SPENT, '#9c8d7d');
	assert.equal(STATUS_AMPLE, STATUS_BURNING);
	assert.equal(STATUS_LOW, STATUS_COOLING);
	assert.equal(STATUS_CRITICAL, STATUS_SPENT);
});

test('thermal scale is ordered, discrete, and readable on the dashboard canvas', () => {
	assert.deepEqual(
		THERMAL_SCALE.map((stop) => stop.name),
		['frost-deep', 'frost', 'pale-warm', 'amber', 'ember-ash', 'ash']
	);
	assert.equal(new Set(THERMAL_SCALE.map((stop) => stop.color)).size, THERMAL_SCALE.length);
	for (const stop of THERMAL_SCALE) {
		assert.ok(contrast(stop.color, '#0c0906') >= 4.5, `${stop.name} must clear WCAG AA`);
	}
});

test('urgency owns three visibly distinct glow treatments', () => {
	assert.equal(urgencyForLevel('burning'), 'calm');
	assert.equal(urgencyForLevel('low'), 'attention');
	assert.equal(urgencyForLevel('spent'), 'alarm');
	assert.notEqual(glowFor('calm', STATUS_BURNING), glowFor('attention', STATUS_BURNING));
	assert.match(glowFor('alarm', STATUS_SPENT), /inset/);
});

test('type reveal uses the approved frontier and logarithmic timing', () => {
	assert.deepEqual(TYPE_REVEAL_GLYPHS, ['░', '▒', '·', '—', '/', '∆']);
	assert.ok(Math.abs(typeRevealProgress(0.3) - 0.6) < 0.02);
	assert.equal(typeRevealProgress(0), 0);
	assert.equal(typeRevealProgress(1), 1);
	assert.equal(typeRevealDuration(0), 500);
	assert.equal(typeRevealDuration(500), 1200);
});

// The reveal froze mid-string and stranded its scramble glyphs because the
// action cancelled its in-flight frame before checking whether the update
// even carried new text. Identity is the whole test: a same-text update must
// neither restart the reveal nor interrupt it.
test('shouldRestartReveal restarts on new text only', () => {
	assert.equal(shouldRestartReveal('', 'reading the ledger…'), true);
	assert.equal(shouldRestartReveal('done', 'running'), true);
	assert.equal(shouldRestartReveal('running', 'running'), false);
	assert.equal(shouldRestartReveal('', ''), false);
});
