import assert from 'node:assert/strict';
import test from 'node:test';

import { DOORS, SHELLS, doorRows, fetchDoorStatus } from './supportMatrix.ts';

function fakeFetch(status: number, body: unknown): typeof fetch {
	return (async () => ({
		ok: status >= 200 && status < 300,
		status,
		json: async () => body
	})) as unknown as typeof fetch;
}

// --- fetchDoorStatus: decoration, never a gate -------------------------------

test('fetchDoorStatus parses a well-formed payload into a slug -> status map', async () => {
	const statuses = await fetchDoorStatus(
		fakeFetch(200, {
			doors: [
				{ slug: 'telegram', status: 'soon' },
				{ slug: 'slack', status: 'live' }
			]
		})
	);
	assert.equal(statuses?.get('telegram'), 'soon');
	assert.equal(statuses?.get('slack'), 'live');
});

test('fetchDoorStatus degrades to null on a non-2xx response, never throws', async () => {
	assert.equal(await fetchDoorStatus(fakeFetch(500, {})), null);
});

test('fetchDoorStatus degrades to null on a network failure, never throws', async () => {
	const throws = (async () => {
		throw new Error('offline');
	}) as unknown as typeof fetch;
	assert.equal(await fetchDoorStatus(throws), null);
});

test('fetchDoorStatus drops a garbage status value rather than passing it through', async () => {
	const statuses = await fetchDoorStatus(
		fakeFetch(200, { doors: [{ slug: 'telegram', status: 'definitely-live-trust-me' }] })
	);
	assert.equal(statuses?.has('telegram'), false, 'an unrecognized status must not survive parsing');
});

test('fetchDoorStatus degrades to null when the body has no doors array', async () => {
	assert.equal(await fetchDoorStatus(fakeFetch(200, { oops: true })), null);
});

// --- doorRows: the status-drift property -------------------------------------
//
// The property under test: a door this landing has not heard a confirmed
// status for must never render as though it were confirmed live. Every
// case below was run against a version of `doorRows` that defaulted a
// missing slug to `'live'` to confirm it actually fails red first.

test('every roster door gets a row even before any fetch resolves', () => {
	const rows = doorRows(null);
	assert.equal(rows.length, DOORS.length);
	assert.ok(rows.every((row) => row.status === null));
});

test('a door the fetch never mentioned reads status: null, not live', () => {
	const statuses = new Map([['telegram', 'live' as const]]);
	const rows = doorRows(statuses);
	const whatsapp = rows.find((row) => row.slug === 'whatsapp');
	assert.equal(whatsapp?.status, null, 'an unconfirmed door must not default to live');
});

test('a door the backend reports as soon renders soon, never gets silently promoted', () => {
	const statuses = new Map([['whatsapp', 'soon' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.find((row) => row.slug === 'whatsapp')?.status, 'soon');
});

test('a door the backend confirms live renders live', () => {
	const statuses = new Map([['signal', 'live' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.find((row) => row.slug === 'signal')?.status, 'live');
});

test('a slug the roster does not know about is ignored, not crashed on', () => {
	const statuses = new Map([['carrier-pigeon', 'live' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.length, DOORS.length);
});

// --- Roster shape --------------------------------------------------------

test('shells and doors rosters carry no duplicate slugs', () => {
	assert.equal(new Set(SHELLS.map((s) => s.slug)).size, SHELLS.length);
	assert.equal(new Set(DOORS.map((d) => d.slug)).size, DOORS.length);
});

test('GitHub keeps its notifications tag — a task channel, not a chat surface', () => {
	assert.equal(DOORS.find((d) => d.slug === 'github')?.tag, 'notifications');
});
