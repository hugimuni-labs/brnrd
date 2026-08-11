import assert from 'node:assert/strict';
import test from 'node:test';

import { digestLastLookedStorageKey, readLastLookedAt, serializeLastLookedAt } from './digest.ts';

const NOW = Date.parse('2026-08-09T20:00:00Z');

test('the storage key is per-account', () => {
	assert.equal(digestLastLookedStorageKey('acc_1'), 'brnrd.digest.lastLookedAt.acc_1');
	assert.notEqual(digestLastLookedStorageKey('acc_1'), digestLastLookedStorageKey('acc_2'));
});

test('a stored anchor round-trips', () => {
	const stored = serializeLastLookedAt(NOW - 5_000);
	assert.equal(readLastLookedAt(stored, NOW), NOW - 5_000);
});

test('corrupt, absent, or future storage reads as never-looked', () => {
	// The anchor must never fabricate a look that didn't happen — a wrong
	// "you saw this" hides exactly the rows the highlight exists to hold.
	assert.equal(readLastLookedAt(null, NOW), null);
	assert.equal(readLastLookedAt('', NOW), null);
	assert.equal(readLastLookedAt('not-a-number', NOW), null);
	assert.equal(readLastLookedAt('-5', NOW), null);
	assert.equal(readLastLookedAt(String(NOW + 60_000), NOW), null);
});

test('serialization truncates to integer milliseconds', () => {
	assert.equal(serializeLastLookedAt(1234.9), '1234');
});
