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
	frontierWidth,
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

// The scramble band must not be a function of how long the string is. The
// duration is capped, so on a long card body the reveal head crosses many
// cells per frame and a fixed 3-cell band leaps over most of them — the
// frontier reads as having stopped scrambling. Simulating the real draw loop
// at a 60fps budget is the only honest check: it is the frame rate, not the
// formula, that decides which cells a reader ever sees scrambled.
function scrambledFraction(length: number): number {
	const duration = typeRevealDuration(length);
	const seen = new Set<number>();
	let previousVisible = 0;
	for (let elapsed = 0; elapsed <= duration; elapsed += 1000 / 60) {
		const visible = Math.floor(typeRevealProgress(elapsed / duration) * length);
		const band = frontierWidth(previousVisible, visible);
		for (let i = visible; i < Math.min(length, visible + band); i += 1) seen.add(i);
		previousVisible = visible;
	}
	return seen.size / length;
}

test('the scramble frontier tracks the reveal head at any text length', () => {
	// Short strings keep the shipped 3-cell band exactly: their head never
	// advances that far in one frame, so this is a widening, not a rewrite.
	assert.equal(frontierWidth(0, 1), 3);
	assert.equal(frontierWidth(10, 12), 3);
	// Long ones widen to whatever the head just crossed.
	assert.equal(frontierWidth(100, 117), 17);

	// A label scrambles every character; a card body must not fall off a
	// cliff behind it. The pre-fix band scrambled 36% of a 600-character body
	// and 18% of a 1200-character one.
	assert.equal(scrambledFraction(20), 1);
	assert.equal(scrambledFraction(60), 1);
	for (const length of [300, 600, 1200, 4000]) {
		assert.ok(
			scrambledFraction(length) > 0.9,
			`${length} chars scrambled only ${(scrambledFraction(length) * 100).toFixed(1)}%`
		);
	}
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
