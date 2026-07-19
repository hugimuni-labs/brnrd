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
	AFTERSHOCK_ODDS,
	AFTERSHOCK_REACH,
	REVEAL_CHAR_BUDGET,
	TYPE_REVEAL_GLYPHS,
	frontierWidth,
	glitchNoise,
	isAftershock,
	revealBudgetMask,
	revealTimeline,
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

// ── One sweep across many spans ──────────────────────────────────────────
//
// A paragraph carrying a link is three spans, not one, because `typeReveal`
// rebuilds the DOM of whatever node it owns and must therefore own a plain
// span *inside* the `<a>` rather than the `<a>` itself. The spans only read as
// one line of streaming text if they solve the same head position, which is
// what the shared `total` buys.

test('a shared sweep tiles the line exactly once', () => {
	const sweep = revealTimeline([5, 3, 12]);
	assert.deepEqual(sweep, [
		{ offset: 0, total: 20 },
		{ offset: 5, total: 20 },
		{ offset: 8, total: 20 }
	]);
	// No gaps, no overlaps: each span starts where the previous one ended.
	assert.equal(sweep[2].offset + 12, sweep[0].total);
	assert.deepEqual(revealTimeline([]), []);
});

test('spans of one sweep agree on the head, and disagree with independent ones', () => {
	const lengths = [5, 3, 12];
	const sweep = revealTimeline(lengths);
	const head = (total: number, ratio: number) => Math.floor(typeRevealProgress(ratio) * total);
	// Halfway through, the shared head sits at one global position; every span
	// derives its own visible count by subtracting its offset, so the counts sum
	// back to that head.
	const shared = head(20, 0.5);
	const perSpan = lengths.map((len, i) => Math.max(0, Math.min(len, shared - sweep[i].offset)));
	assert.equal(
		perSpan.reduce((a, b) => a + b, 0),
		Math.min(20, shared)
	);
	// Without the shared total each span would run its own curve and the last
	// span — the longest — would be further along than the first. That is the
	// "three simultaneous typewriters" look this replaces.
	assert.ok(head(12, 0.5) / 12 > head(20, 0.5) / 20 - 0.5);
	assert.notEqual(perSpan[2], head(12, 0.5));
});

// ── The glitch ───────────────────────────────────────────────────────────

test('cell noise is deterministic, decorrelated, and in range', () => {
	assert.equal(glitchNoise(7, 3), glitchNoise(7, 3));
	assert.notEqual(glitchNoise(7, 3), glitchNoise(8, 3));
	assert.notEqual(glitchNoise(7, 3), glitchNoise(7, 4));
	const samples = [];
	for (let i = 0; i < 400; i += 1) samples.push(glitchNoise(i, i % 7));
	assert.ok(samples.every((n) => n >= 0 && n < 1));
	const mean = samples.reduce((a, b) => a + b, 0) / samples.length;
	assert.ok(Math.abs(mean - 0.5) < 0.06, `mean ${mean}`);
});

test('aftershocks re-corrupt settled text near the head, and nowhere else', () => {
	// Ahead of the head is the frontier's job, not the aftershock's.
	assert.equal(isAftershock(10, 10, 0), false);
	assert.equal(isAftershock(11, 10, 0), false);
	// Far behind the head the text is finished and must stay finished —
	// otherwise a reader watching the end of a long paragraph sees the
	// beginning still flickering.
	for (let frame = 0; frame < 200; frame += 1) {
		assert.equal(isAftershock(0, AFTERSHOCK_REACH + 1, frame), false);
	}
	// Inside the trailing window it fires at roughly the stated odds: dense
	// enough to read as instability, sparse enough to stay legible.
	let fired = 0;
	let eligible = 0;
	for (let frame = 0; frame < 400; frame += 1) {
		for (let behind = 1; behind <= AFTERSHOCK_REACH; behind += 1) {
			eligible += 1;
			if (isAftershock(200 - behind, 200, frame)) fired += 1;
		}
	}
	const rate = fired / eligible;
	assert.ok(Math.abs(rate - AFTERSHOCK_ODDS) < 0.03, `rate ${rate}`);
	assert.ok(rate < 0.2, 'a glitch that fires on a fifth of the text is unreadable');
});

// ── The budget ───────────────────────────────────────────────────────────
//
// Replaces the old per-page opt-out. The reveal is a flourish over what a
// reader lands on, so it is bounded in characters rather than refused on any
// document that happens to be long.

test('the reveal budget covers the opening of a page and then stops', () => {
	assert.deepEqual(revealBudgetMask([10, 10, 10], 100), [true, true, true]);
	// The block that crosses the line still reveals — truncating mid-block
	// would show one paragraph half streaming and half painted.
	assert.deepEqual(revealBudgetMask([60, 60, 60], 100), [true, true, false]);
	assert.deepEqual(revealBudgetMask([500], 100), [true]);
	// Code blocks report zero length, so they neither reveal nor consume.
	assert.deepEqual(revealBudgetMask([0, 0, 40], 100), [true, true, true]);
	// A kb log page is thousands of blocks; almost none of them animate.
	const many = Array.from({ length: 2000 }, () => 200);
	const mask = revealBudgetMask(many);
	assert.ok(mask.filter(Boolean).length <= REVEAL_CHAR_BUDGET / 200 + 1);
	assert.equal(mask.at(-1), false);
});

// ── The boot gate ────────────────────────────────────────────────────────
//
// The reveal now waits on the layout's boot curtain. Waiting text is invisible
// text, so the failure mode of this gate is a blank page, not a missing
// flourish — which is why the fallback below is not optional.

test('boot waiters run once, in order, and can be cancelled', async () => {
	const { markBooted, whenBooted, isBooted, resetBootForTest } = await import('./boot.ts');
	resetBootForTest();
	const ran: string[] = [];
	whenBooted(() => ran.push('a'));
	const cancel = whenBooted(() => ran.push('cancelled'));
	whenBooted(() => ran.push('b'));
	cancel();
	assert.equal(isBooted(), false);
	assert.deepEqual(ran, [], 'nothing may run before the curtain lifts');

	markBooted();
	assert.deepEqual(ran, ['a', 'b']);
	assert.equal(isBooted(), true);

	// A second lift is a no-op, not a replay: the curtain rises once and the
	// fallback timer may well fire after it.
	markBooted();
	assert.deepEqual(ran, ['a', 'b']);

	// Past boot, work runs synchronously — a panel expanding at t=30s must not
	// queue behind a signal that already happened.
	whenBooted(() => ran.push('late'));
	assert.deepEqual(ran, ['a', 'b', 'late']);
	resetBootForTest();
});
