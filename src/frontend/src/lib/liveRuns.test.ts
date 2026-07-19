import assert from 'node:assert/strict';
import test from 'node:test';

import { LiveRunsAuthError, liveRunDisplayName, requestRunStop } from './liveRuns.ts';

test('live run display prefers the resident-authored name', () => {
	assert.equal(liveRunDisplayName({ name: 'run naming', label: 'waking message', kind: 'daemon' }), 'run naming');
});

test('live run display falls back to the waking-message excerpt', () => {
	assert.equal(liveRunDisplayName({ name: '', label: 'waking message', kind: 'daemon' }), 'waking message');
});

// #476: a tap that gets swallowed must never be silent — the caller can only
// keep that promise if this layer throws something it can name.

function stubFetch(status: number, body: unknown = {}): typeof fetch {
	return (async () =>
		({
			ok: status >= 200 && status < 300,
			status,
			json: async () => body
		}) as Response) as unknown as typeof fetch;
}

test('a parked stop comes back as a pending request', async () => {
	const row = await requestRunStop(
		'run-b',
		stubFetch(200, {
			stop_request: {
				request_id: 'stopreq-1',
				run_id: 'run-b',
				requested_at: null,
				status: 'pending'
			}
		})
	);
	assert.equal(row.run_id, 'run-b');
	assert.equal(row.status, 'pending');
});

test('an expired session is typed, so the cell can say "sign in again"', async () => {
	await assert.rejects(() => requestRunStop('run-b', stubFetch(401)), LiveRunsAuthError);
});

test('a run that ended first says so rather than failing anonymously', async () => {
	await assert.rejects(
		() => requestRunStop('run-b', stubFetch(404)),
		/no longer live/
	);
});

test('the run id is encoded, not interpolated raw', async () => {
	let seen = '';
	const spy = (async (url: string) => {
		seen = url;
		return { ok: true, status: 200, json: async () => ({ stop_request: {} }) } as Response;
	}) as unknown as typeof fetch;
	await requestRunStop('run/../evil', spy);
	assert.ok(!seen.includes('run/../evil'), 'a slash in a handle must not reshape the path');
});
