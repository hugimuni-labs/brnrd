import assert from 'node:assert/strict';
import test from 'node:test';

import { requestWake } from './runners.ts';

test('wake request carries the complete dispatch choice', async () => {
	let requestBody: unknown = null;
	const fetchImpl = (async (_input: RequestInfo | URL, init?: RequestInit) => {
		requestBody = JSON.parse(String(init?.body ?? '{}'));
		return new Response(
			JSON.stringify({
				wake_request: {
					request_id: 'wake-1',
					profile: 'codex',
					repo_label: 'hugimuni-labs/brnrd',
					environment: 'solitary',
					requested_at: null,
					status: 'pending'
				}
			}),
			{ status: 200, headers: { 'content-type': 'application/json' } }
		);
	}) as typeof fetch;

	await requestWake(
		'codex',
		{ repo_label: 'hugimuni-labs/brnrd', environment: 'solitary' },
		fetchImpl
	);

	assert.deepEqual(requestBody, {
		profile: 'codex',
		repo_label: 'hugimuni-labs/brnrd',
		environment: 'solitary'
	});
});
