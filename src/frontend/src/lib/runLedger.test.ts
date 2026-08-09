import assert from 'node:assert/strict';
import test from 'node:test';

import {
	ageLabel,
	ageSince,
	dateTimeLabel,
	relicIcon,
	relicLabel,
	servedWindowMs
} from './runLedger.ts';

test('unknown relics use the first non-empty descriptive field', () => {
	assert.equal(
		relicLabel({ kind: 'artifact', text: '', path: 'report.md', note: 'later' }),
		'report.md'
	);
	assert.equal(relicLabel({ kind: 'artifact', note: 'deployed' }), 'deployed');
	assert.equal(relicLabel({ kind: 'artifact' }), 'artifact');
	assert.equal(relicIcon('artifact'), '•');
});

test('reply relics prefer their archived content excerpt', () => {
	assert.equal(relicLabel({ kind: 'reply', excerpt: 'Shipped the fix.' }), 'Shipped the fix.');
	assert.equal(relicLabel({ kind: 'reply' }), 'reply');
});

test('dateTimeLabel adds the local date once an instant is older than today', () => {
	const now = new Date(2026, 6, 29, 16, 0, 0);
	const today = new Date(2026, 6, 29, 10, 5, 0);
	const older = new Date(2026, 6, 26, 10, 5, 0);

	assert.equal(dateTimeLabel(today.toISOString(), now.getTime()), today.toLocaleTimeString());
	assert.equal(dateTimeLabel(older.toISOString(), now.getTime()), older.toLocaleString());
	assert.equal(dateTimeLabel('not-an-instant', now.getTime()), '—');
	assert.equal(dateTimeLabel(null, now.getTime()), '—');
});

test('ageLabel: relative under an hour, viewer-clock at or beyond it (#1256)', () => {
	const now = new Date(2026, 6, 29, 16, 0, 0).getTime();

	assert.equal(ageLabel(now - 30_000, now), 'just now');
	assert.equal(ageLabel(now - 59_000, now), 'just now');
	assert.equal(ageLabel(now - 24 * 60_000, now), '24m ago');
	assert.equal(ageLabel(now - 59 * 60_000, now), '59m ago');
	assert.equal(ageLabel(now, now), 'just now', 'a non-negative delta never reads as future');
	assert.equal(ageLabel(now + 60_000, now), 'just now', 'a clock-skewed future instant clamps');
});

test('ageLabel: at the 1h boundary and beyond, same day stays a bare clock, otherwise gains a date', () => {
	// Comfortably midday, so the ±hours below never cross a calendar
	// boundary by accident in any plausible test-runner timezone.
	const now = new Date(2026, 6, 29, 16, 0, 0);
	const oneHourAgo = new Date(2026, 6, 29, 15, 0, 0);
	const fiveHoursAgo = new Date(2026, 6, 29, 11, 0, 0);
	const yesterday = new Date(2026, 6, 28, 15, 0, 0);

	assert.equal(
		ageLabel(oneHourAgo.getTime(), now.getTime()),
		oneHourAgo.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
		'exactly 1h already reads as a clock, not "60m ago"'
	);
	assert.equal(
		ageLabel(fiveHoursAgo.getTime(), now.getTime()),
		fiveHoursAgo.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
		'same day: bare clock, no date prefix'
	);
	assert.equal(
		ageLabel(yesterday.getTime(), now.getTime()),
		`${yesterday.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${yesterday.toLocaleTimeString(
			[],
			{ hour: '2-digit', minute: '2-digit' }
		)}`,
		'not today: the date leads the clock, or a stale reading passes for fresh'
	);
});

test('ageSince: null/unparseable timestamps render nothing, a real one goes through ageLabel', () => {
	const now = new Date(2026, 6, 29, 16, 0, 0);
	const started = new Date(2026, 6, 29, 15, 36, 0);

	assert.equal(ageSince(null, now.getTime()), null);
	assert.equal(ageSince('not-an-instant', now.getTime()), null);
	assert.equal(ageSince(started.toISOString(), now.getTime()), '24m ago');
});

test('servedWindowMs falls back rather than handing the cloth a NaN window', () => {
	const fallback = 30 * 24 * 60 * 60 * 1000;

	// The honest cases: a served span wins, `null` (no span requested) falls back.
	assert.equal(servedWindowMs(7 * 24 * 3600, fallback), 7 * 24 * 3600 * 1000);
	assert.equal(servedWindowMs(null, fallback), fallback);

	// The case this function exists for: a server too old to send the field at
	// all. `undefined * 1000` is NaN, and `ageMs <= NaN` is false for every
	// row, so the cloth would render empty and say nothing about why — the
	// same "a narrowed surface renders as if it hadn't" defect #994 fixes.
	assert.equal(servedWindowMs(undefined, fallback), fallback);
	assert.equal(servedWindowMs(Number.NaN, fallback), fallback);
	assert.equal(servedWindowMs('2592000', fallback), fallback);
	assert.equal(servedWindowMs(0, fallback), fallback);
	assert.equal(servedWindowMs(-1, fallback), fallback);
});
