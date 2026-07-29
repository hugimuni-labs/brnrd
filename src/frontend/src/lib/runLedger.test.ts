import assert from 'node:assert/strict';
import test from 'node:test';

import { dateTimeLabel, relicIcon, relicLabel } from './runLedger.ts';

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
