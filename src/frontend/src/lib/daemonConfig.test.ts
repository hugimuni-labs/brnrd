import assert from 'node:assert/strict';
import test from 'node:test';

import { fetchDaemonConfig } from './daemonConfig.ts';

test('daemon config reads the merged provenance table', async () => {
	const rows = await fetchDaemonConfig(
		async () =>
			new Response(
				JSON.stringify({
					config: [{ key: 'runner.default', value: 'codex', source: 'daemon.config' }]
				})
			)
	);
	assert.deepEqual(rows, [{ key: 'runner.default', value: 'codex', source: 'daemon.config' }]);
});
