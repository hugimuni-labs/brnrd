import assert from 'node:assert/strict';
import test from 'node:test';

import {
	DOORS,
	REACH_GROUPS,
	SHELLS,
	doorRows,
	fetchDoorStatus,
	reachBadge
} from './supportMatrix.ts';

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
				{ slug: 'slack', status: 'live' },
				{ slug: 'signal', status: 'ready' }
			]
		})
	);
	assert.equal(statuses?.get('telegram'), 'soon');
	assert.equal(statuses?.get('slack'), 'live');
	assert.equal(statuses?.get('signal'), 'ready', '"ready" is a real backend status, not garbage');
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

// --- doorRows: backend status drift ------------------------------------------

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

test('a door the backend reports as soon stays soon', () => {
	const statuses = new Map([['whatsapp', 'soon' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.find((row) => row.slug === 'whatsapp')?.status, 'soon');
});

test('a door the backend confirms live renders live', () => {
	const statuses = new Map([['signal', 'live' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.find((row) => row.slug === 'signal')?.status, 'live');
});

test('a door the backend reports as ready stays ready internally', () => {
	const statuses = new Map([['slack', 'ready' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.find((row) => row.slug === 'slack')?.status, 'ready');
});

test('a slug the roster does not know about is ignored, not crashed on', () => {
	const statuses = new Map([['carrier-pigeon', 'live' as const]]);
	const rows = doorRows(statuses);
	assert.equal(rows.length, DOORS.length);
});

// --- Landing reach topology --------------------------------------------------

function surface(id: string) {
	const found = REACH_GROUPS.flatMap((group) => group.surfaces).find((item) => item.id === id);
	assert.ok(found, `missing reach surface ${id}`);
	return found;
}

test('Telegram is represented as two different routes, not one provider tile', () => {
	const hosted = surface('telegram-hosted');
	const byo = surface('telegram-byo');
	assert.equal(hosted.statusMode, 'hosted');
	assert.equal(hosted.doorSlug, 'telegram');
	assert.equal(byo.statusMode, 'byo');
	assert.notEqual(hosted.id, byo.id);
});

test('GitHub and Slack are app-shaped integrations', () => {
	const apps = REACH_GROUPS.find((group) => group.slug === 'apps');
	assert.deepEqual(
		apps?.surfaces.map((item) => item.id),
		['github-app', 'slack-app']
	);
});

test('Signal is BYO and the dashboard is control, not peers in one connector list', () => {
	assert.equal(surface('signal-byo').statusMode, 'byo');
	assert.equal(REACH_GROUPS.find((group) => group.slug === 'control')?.surfaces[0]?.id, 'web-dashboard');
});

test('every hosted reach surface points at a known backend door slug', () => {
	const known = new Set(DOORS.map((door) => door.slug));
	for (const item of REACH_GROUPS.flatMap((group) => group.surfaces)) {
		if (item.statusMode !== 'hosted') continue;
		assert.ok(item.doorSlug && known.has(item.doorSlug), `${item.id} has an unknown door slug`);
	}
});

test('landing maps internal ready/soon to coming rather than exposing implementation state', () => {
	const slack = surface('slack-app');
	assert.equal(reachBadge(slack, new Map([['slack', 'ready' as const]])), 'coming');
	assert.equal(reachBadge(slack, new Map([['slack', 'soon' as const]])), 'coming');
});

test('landing only calls a hosted route live after the backend confirms it', () => {
	const telegram = surface('telegram-hosted');
	assert.equal(reachBadge(telegram, new Map([['telegram', 'live' as const]])), 'live');
	assert.equal(reachBadge(telegram, null), 'checking');
});

test('BYO routes do not inherit brnrd.dev hosted status', () => {
	const telegram = surface('telegram-byo');
	assert.equal(reachBadge(telegram, new Map([['telegram', 'soon' as const]])), 'byo');
	assert.equal(reachBadge(telegram, null), 'byo');
});

// --- Roster shape -------------------------------------------------------------

test('shells and doors rosters carry no duplicate slugs', () => {
	assert.equal(new Set(SHELLS.map((s) => s.slug)).size, SHELLS.length);
	assert.equal(new Set(DOORS.map((d) => d.slug)).size, DOORS.length);
});

test('reach surface ids are unique even when a platform legitimately repeats', () => {
	const ids = REACH_GROUPS.flatMap((group) => group.surfaces.map((item) => item.id));
	assert.equal(new Set(ids).size, ids.length);
});

test('GitHub keeps its notifications tag in the backend roster', () => {
	assert.equal(DOORS.find((d) => d.slug === 'github')?.tag, 'notifications');
});
