import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	backchannelChip,
	backchannelCount,
	backchannelShowClear,
	buildBackchannelItems
} from './backchannel.ts';
import type { ConfigChangeRequestItem } from './configRequests.ts';
import type { PRReviewItem } from './prReviewQueue.ts';

test('backchannel count spans both review and config queues', () => {
	const prs: PRReviewItem[] = [
		{
			number: 1,
			title: '',
			url: '',
			repo_label: '',
			created_at: null,
			draft: false,
			author: ''
		},
		{
			number: 2,
			title: '',
			url: '',
			repo_label: '',
			created_at: null,
			draft: false,
			author: ''
		}
	];
	const requests: ConfigChangeRequestItem[] = [
		{
			id: 'cfg-1',
			repo_label: '',
			config_key: '',
			current_value: '',
			requested_value: '',
			reason: '',
			created_at: null,
			expires_at: null,
			approve_url: ''
		}
	];
	assert.equal(backchannelCount([], []), 0);
	assert.equal(backchannelCount(prs, requests), 3);
});

test('backchannel items merge the two queues oldest-first', () => {
	const items = buildBackchannelItems(
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

test('the clear verdict waits for every feed — a mid-load zero is counting, not clear', () => {
	// The measured 2026-08-01 flicker: derived feeds arrive as [] while the
	// authored surface file is still in flight → count 0, feeds unresolved.
	assert.equal(backchannelShowClear(false, 0, false), false);
	assert.equal(backchannelChip(false, 0), 'counting…');
});

test('a genuinely empty resolved queue is clear', () => {
	assert.equal(backchannelShowClear(true, 0, false), true);
	assert.equal(backchannelChip(true, 0), 'nothing waiting');
});

test('withheld is never rendered as clear', () => {
	assert.equal(backchannelShowClear(true, 0, true), false);
});

test('a partial sum is labeled as partial, a resolved sum as the verdict', () => {
	assert.equal(backchannelChip(false, 4), '4 so far…');
	assert.equal(backchannelChip(true, 4), '4 items waiting');
	assert.equal(backchannelChip(true, 1), '1 item waiting');
});
