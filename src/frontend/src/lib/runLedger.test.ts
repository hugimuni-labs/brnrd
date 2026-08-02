import assert from 'node:assert/strict';
import test from 'node:test';

import { dateTimeLabel, relicIcon, relicLabel, servedWindowMs } from './runLedger.ts';

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
