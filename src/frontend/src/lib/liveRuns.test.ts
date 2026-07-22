import assert from 'node:assert/strict';
import test from 'node:test';

import {
	LiveRunsAuthError,
	fetchLiveRuns,
	heartbeatLevel,
	liveRelicChips,
	liveRunDisplayName,
	requestRunStop
} from './liveRuns.ts';

test('live run display prefers the resident-authored name', () => {
	assert.equal(
		liveRunDisplayName({ name: 'run naming', label: 'waking message', kind: 'daemon' }),
		'run naming'
	);
});

test('live run display falls back to the waking-message excerpt', () => {
	assert.equal(
		liveRunDisplayName({ name: '', label: 'waking message', kind: 'daemon' }),
		'waking message'
	);
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
	await assert.rejects(() => requestRunStop('run-b', stubFetch(404)), /no longer live/);
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

// ── relics-so-far chips (#342) ──────────────────────────────────────

test('relic chips order produce first and keep unknown kinds', () => {
	assert.deepEqual(liveRelicChips({ kb: 1, commit: 2, artifact: 3, pr: 1 }), [
		{ kind: 'commit', count: 2 },
		{ kind: 'pr', count: 1 },
		{ kind: 'kb', count: 1 },
		{ kind: 'artifact', count: 3 }
	]);
});

test('branch and summary never chip — they restate other produce', () => {
	assert.deepEqual(liveRelicChips({ branch: 1, summary: 1, commit: 2 }), [
		{ kind: 'commit', count: 2 }
	]);
});

test('zero, empty, and absent counts render no chips at all', () => {
	assert.deepEqual(liveRelicChips(null), []);
	assert.deepEqual(liveRelicChips(undefined), []);
	assert.deepEqual(liveRelicChips({}), []);
	assert.deepEqual(liveRelicChips({ commit: 0 }), []);
});

// ── Reconnect/refetch behavior (#421) ──────────────────────────────────────
//
// When the daemon dev-reloads (reexecs at a run boundary), the brnrd server
// briefly stops receiving PUT /v1/daemons/live-runs updates and marks its
// snapshot stale.  The client poll at 2s picks up the next successful response
// automatically — but two distinct failure modes need test cover:
//
// 1. Stale snapshot (brnrd server is up, daemon is between exec phases):
//    `stale: true` in the response → heartbeatLevel returns 'unknown' so
//    individual run cards don't claim a freshness they can't guarantee.
//
// 2. Transient fetch error (5xx during a deploy window, or network hiccup):
//    must NOT throw a LiveRunsAuthError — the page code uses that distinction
//    to decide whether to keep showing last-known data (non-auth error) or
//    stop polling entirely (401 = session gone).

test('stale snapshot marks heartbeat level as unknown, not running', () => {
	// During a daemon dev-reload brnrd marks the snapshot stale; the card
	// must not claim "running" on data it knows is no longer fresh.
	const recentIso = new Date(Date.now() - 1000).toISOString();
	assert.equal(heartbeatLevel(recentIso, Date.now(), true), 'unknown');
});

test('heartbeat level recovers to running once the stale flag clears', () => {
	const now = Date.now();
	const recentIso = new Date(now - 5000).toISOString();
	assert.equal(heartbeatLevel(recentIso, now, false), 'running');
});

test('a transient 5xx during reexec window is not a LiveRunsAuthError', async () => {
	// A 503 during a deploy/reexec window must propagate as a plain Error
	// so the page can keep showing last-known live runs instead of wiping
	// them (which it would do on a 401/LiveRunsAuthError, since that means
	// the session is actually gone — a fundamentally different state).
	await assert.rejects(
		() => fetchLiveRuns(stubFetch(503)),
		(e: unknown) => {
			assert.ok(!(e instanceof LiveRunsAuthError), 'must not be an auth error');
			assert.ok(e instanceof Error);
			assert.ok(e.message.includes('503'));
			return true;
		}
	);
});

test('a 401 during the poll is a LiveRunsAuthError, not a plain error', async () => {
	// The page stops polling on 401 (session gone) — verify the typed
	// distinction is preserved so the two code paths cannot silently swap.
	await assert.rejects(
		() => fetchLiveRuns(stubFetch(401)),
		LiveRunsAuthError
	);
});
