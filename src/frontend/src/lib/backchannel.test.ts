import assert from 'node:assert/strict';
import { test } from 'node:test';
import { backchannelCount, buildBackchannelItems } from './backchannel.ts';

test('backchannel count spans both review and config queues', () => {
	assert.equal(backchannelCount([], []), 0);
	assert.equal(
		backchannelCount([{ number: 1 } as any, { number: 2 } as any], [{ id: 'cfg-1' } as any]),
		3
	);
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
