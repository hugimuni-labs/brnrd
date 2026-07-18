import assert from 'node:assert/strict';
import test from 'node:test';

import { fetchRunLedger } from './runLedger.ts';

test('fetchRunLedger sends the selected shelf span to the existing endpoint', async () => {
	let requested = '';
	const fakeFetch = (async (input: RequestInfo | URL) => {
		requested = String(input);
		return new Response(
			JSON.stringify({ generated_at: '', rows: [], stale: false, reported_at: null })
		);
	}) as typeof fetch;

	await fetchRunLedger(fakeFetch, 256, 3 * 24 * 60 * 60 * 1000);

	assert.equal(requested, '/v1/dashboard/run-ledger?limit=256&span_seconds=259200');
});
