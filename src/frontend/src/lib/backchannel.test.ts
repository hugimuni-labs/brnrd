import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	buildDerivedAsks,
	derivedAsksChip,
	derivedAsksShowClear,
	draftPrCount
} from './backchannel.ts';
import type { PRReviewItem } from './prReviewQueue.ts';

test('derived asks merge the two queues oldest-first', () => {
	const items = buildDerivedAsks(
		[
			{
				number: 42,
				title: 'Ship the thing',
				url: 'https://example.test/pr/42',
				repo_label: 'hugimuni-labs/brnrd',
				created_at: '2026-07-29T10:00:00Z',
				draft: false,
				author: 'brnrd-bot'
			}
		],
		[
			{
				id: 'cfg-1',
				repo_label: 'hugimuni-labs/brnrd',
				config_key: 'runner.shell',
				current_value: 'claude',
				requested_value: 'codex',
				reason: 'economy',
				created_at: '2026-07-29T09:00:00Z',
				expires_at: null,
				approve_url: 'https://example.test/config/cfg-1'
			}
		]
	);
	assert.deepEqual(
		items.map((item) => [item.kind, item.statusLabel, item.linkLabel]),
		[
			['config', 'decide', 'decide'],
			['pr', 'review', 'open']
		]
	);
	assert.equal(items[0].headline, 'runner.shell: claude → codex');
	assert.equal(items[1].headline, '#42 Ship the thing');
});

function pr(overrides: Partial<PRReviewItem>): PRReviewItem {
	return {
		number: 1,
		title: '',
		url: '',
		repo_label: '',
		created_at: null,
		draft: false,
		author: '',
		...overrides
	};
}

test('a draft PR means the resident is not done with it — never a needs-you row', () => {
	// Maintainer, 08-11: "5 of 7 in draft, showing them as needing user
	// attention feels like a lie." Filtered at the builder so the count,
	// the chip, and the rows all agree — no consumer can drift out of sync.
	const items = buildDerivedAsks(
		[pr({ number: 1, draft: true }), pr({ number: 2, draft: false })],
		[]
	);
	assert.deepEqual(
		items.map((item) => item.headline),
		['#2 Untitled PR']
	);
});

test('a fully-draft queue derives to nothing waiting, not a zero verdict on a lie', () => {
	const items = buildDerivedAsks([pr({ number: 1, draft: true })], []);
	assert.deepEqual(items, []);
});

test('draftPrCount counts only what buildDerivedAsks withheld', () => {
	assert.equal(draftPrCount(null), 0);
	assert.equal(draftPrCount(undefined), 0);
	assert.equal(draftPrCount([]), 0);
	assert.equal(draftPrCount([pr({ number: 1, draft: true }), pr({ number: 2, draft: false })]), 1);
});

test('the clear verdict waits for every feed — a mid-load zero is counting, not clear', () => {
	// The measured 2026-08-01 flicker: derived feeds arrive as [] while
	// another feed is still in flight → count 0, feeds unresolved.
	assert.equal(derivedAsksShowClear(false, 0, false), false);
	assert.equal(derivedAsksChip(false, 0), 'counting…');
});

test('a genuinely empty resolved queue is clear', () => {
	assert.equal(derivedAsksShowClear(true, 0, false), true);
	assert.equal(derivedAsksChip(true, 0), 'nothing waiting');
});

test('withheld is never rendered as clear', () => {
	assert.equal(derivedAsksShowClear(true, 0, true), false);
});

test('the chip states a bare derived count once resolved — one population, no attribution needed', () => {
	assert.equal(derivedAsksChip(true, 4), '4 derived');
	assert.equal(derivedAsksChip(true, 1), '1 derived');
});

test('a partial sum stays labeled as still counting', () => {
	assert.equal(derivedAsksChip(false, 3), '3 derived · counting…');
});
