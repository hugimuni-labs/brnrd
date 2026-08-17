import assert from 'node:assert/strict';
import test from 'node:test';

import {
	connectRepo,
	fetchPairedChats,
	fetchTelegramPairStatus,
	mintAccountTelegramPair,
	ReposAuthError,
	revokePairedChat,
	setPublishLayers,
	splitPairingCommand,
	telegramPairLabel
} from './repos.ts';

function fakeFetch(status: number, body: unknown): typeof fetch {
	const calls: { url: string; init?: RequestInit }[] = [];
	const impl = (async (url: string, init?: RequestInit) => {
		calls.push({ url, init });
		return {
			ok: status >= 200 && status < 300,
			status,
			json: async () => body
		} as Response;
	}) as unknown as typeof fetch;
	(impl as unknown as { calls: typeof calls }).calls = calls;
	return impl;
}

function calls(impl: typeof fetch) {
	return (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
}

test('connectRepo sends publish_layers, defaulting to empty when omitted', async () => {
	const impl = fakeFetch(200, { ok: true, notice: 'Repo enabled.' });
	await connectRepo({ repo_full_name: 'Gurio/brr' }, impl);
	const body = JSON.parse(String(calls(impl)[0].init?.body));
	assert.equal(body.publish_layers, '');
});

test('connectRepo passes an explicit publish_layers choice through untouched', async () => {
	const impl = fakeFetch(200, { ok: true, notice: 'Repo enabled.' });
	await connectRepo({ repo_full_name: 'Gurio/brr', publish_layers: 'activity,quota' }, impl);
	const body = JSON.parse(String(calls(impl)[0].init?.body));
	assert.equal(body.publish_layers, 'activity,quota');
});

test('setPublishLayers posts to the per-repo settings endpoint', async () => {
	const impl = fakeFetch(200, { ok: true, notice: 'Publish scope updated.' });
	const result = await setPublishLayers('repo_1', 'corpus', impl);
	assert.equal(calls(impl)[0].url, '/v1/repos/repo_1/publish-layers');
	assert.equal(calls(impl)[0].init?.method, 'POST');
	assert.deepEqual(JSON.parse(String(calls(impl)[0].init?.body)), { publish_layers: 'corpus' });
	assert.equal(result.ok, true);
});

test('setPublishLayers escapes the repo id as a path segment', async () => {
	const impl = fakeFetch(200, { ok: true, notice: 'Publish scope updated.' });
	await setPublishLayers('repo/../x', 'none', impl);
	assert.equal(calls(impl)[0].url, '/v1/repos/repo%2F..%2Fx/publish-layers');
});

// #1277a: the maintainer's own report — a COPY button that hands over
// `cd <repo>`, a literal placeholder no shell can run, along with the line
// that actually is. `splitPairingCommand` is the one place either
// ColdStart.svelte or /repos/+page.svelte parses the backend's two-line
// spelling, so this pins the split itself rather than each caller's markup.
test('splitPairingCommand separates the cd line from the runnable command', () => {
	const parts = splitPairingCommand('cd <repo>\nbrnrd account connect https://brnrd.dev');
	assert.equal(parts.setupLine, 'cd <repo>');
	assert.equal(parts.runnable, 'brnrd account connect https://brnrd.dev');
});

test('splitPairingCommand carries a real checkout name through unchanged', () => {
	const parts = splitPairingCommand('cd brr\nbrnrd account connect https://brnrd.dev');
	assert.equal(parts.setupLine, 'cd brr');
	assert.equal(parts.runnable, 'brnrd account connect https://brnrd.dev');
});

// A single-line command (today's shape never sends one, but the parser must
// not invent a blank prose line for a string that was already whole) is
// entirely runnable — nothing to split out.
test('splitPairingCommand treats a single-line command as entirely runnable', () => {
	const parts = splitPairingCommand('brnrd account connect https://brnrd.dev');
	assert.equal(parts.setupLine, null);
	assert.equal(parts.runnable, 'brnrd account connect https://brnrd.dev');
});

// #885: "pair telegram button is always there no matter if it paired or
// not" — the render decision (which button, which label) collapses to this
// pure (paired, busy) -> label mapping.
test('telegramPairLabel: unpaired and idle reads "pair Telegram"', () => {
	assert.equal(telegramPairLabel(false, false), 'pair Telegram');
});

test('telegramPairLabel: unpaired and busy reads "pairing"', () => {
	assert.equal(telegramPairLabel(false, true), 'pairing');
});

test('telegramPairLabel: paired and idle reads the disclosed "re-pair"', () => {
	assert.equal(telegramPairLabel(true, false), 're-pair Telegram');
});

test('telegramPairLabel: paired and busy reads "re-pairing"', () => {
	assert.equal(telegramPairLabel(true, true), 're-pairing Telegram');
});

// #1457 — the account-level mint the mobile cold-start CTA calls, distinct
// from the repo-scoped `pairRepoTelegram` above (`/v1/repos/<id>/telegram-
// pair`, 404s with no connected repo). No body, session-cookie auth only.
test('mintAccountTelegramPair posts to the account-level endpoint with no body', async () => {
	const impl = fakeFetch(200, {
		pair_code: 'ABCD1234',
		instructions: 'send /start ABCD1234',
		deep_link: 'https://t.me/brnrdbot?start=ABCD1234'
	});
	const result = await mintAccountTelegramPair(impl);
	assert.equal(calls(impl)[0].url, '/v1/dashboard/telegram-pair');
	assert.equal(calls(impl)[0].init?.method, 'POST');
	assert.equal(calls(impl)[0].init?.body, undefined);
	assert.deepEqual(result, {
		pair_code: 'ABCD1234',
		instructions: 'send /start ABCD1234',
		deep_link: 'https://t.me/brnrdbot?start=ABCD1234'
	});
});

// A bot handle that failed #1242's shape check server-side mints a code
// with no deep link — the caller (ColdStart.svelte) falls back to showing
// the code itself, so the wire shape allowing `deep_link: null` must round
// trip untouched, not get coerced into an empty string or dropped.
test('mintAccountTelegramPair carries a null deep_link through untouched', async () => {
	const impl = fakeFetch(200, {
		pair_code: 'WXYZ5678',
		instructions: 'send /start WXYZ5678',
		deep_link: null
	});
	const result = await mintAccountTelegramPair(impl);
	assert.equal(result.deep_link, null);
});

test('mintAccountTelegramPair raises ReposAuthError on 401', async () => {
	const impl = fakeFetch(401, { detail: 'unauthenticated' });
	await assert.rejects(() => mintAccountTelegramPair(impl), ReposAuthError);
});

test('mintAccountTelegramPair raises on a non-401 error status', async () => {
	const impl = fakeFetch(503, { detail: 'could not allocate pair code' });
	await assert.rejects(() => mintAccountTelegramPair(impl), /telegram pair mint failed: 503/);
});

// #1464 — the minting session's outcome readback: ColdStart polls this by
// the pair_code it just minted, keyed to whoever's session minted it.
test('fetchTelegramPairStatus reads the code as a path segment', async () => {
	const impl = fakeFetch(200, { consumed: false, display: null });
	const result = await fetchTelegramPairStatus('PK-AB12', impl);
	assert.equal(calls(impl)[0].url, '/v1/dashboard/telegram-pair/PK-AB12');
	assert.deepEqual(result, { consumed: false, display: null });
});

test('fetchTelegramPairStatus carries the redeemed display through untouched', async () => {
	const impl = fakeFetch(200, { consumed: true, display: '@ada_l' });
	const result = await fetchTelegramPairStatus('PK-AB12', impl);
	assert.deepEqual(result, { consumed: true, display: '@ada_l' });
});

test('fetchTelegramPairStatus raises ReposAuthError on 401', async () => {
	const impl = fakeFetch(401, { detail: 'unauthenticated' });
	await assert.rejects(() => fetchTelegramPairStatus('PK-AB12', impl), ReposAuthError);
});

test('fetchTelegramPairStatus raises on a non-401 error status (e.g. a code minted by another account)', async () => {
	const impl = fakeFetch(404, { detail: 'unknown pair code' });
	await assert.rejects(
		() => fetchTelegramPairStatus('PK-AB12', impl),
		/pair status fetch failed: 404/
	);
});

// #1464 — the paired-chats list: platform / chat title / principal display
// / paired-at, one row per ChannelRoute.
test('fetchPairedChats reads the account-scoped list endpoint', async () => {
	const impl = fakeFetch(200, {
		paired_chats: [
			{
				id: 'chan_1',
				platform: 'telegram',
				chat_title: null,
				principal_display: '@ada_l',
				paired_at: '2026-08-17T20:00:00+00:00',
				paired_at_label: '5m ago',
				repo_full_name: null
			}
		]
	});
	const result = await fetchPairedChats(impl);
	assert.equal(calls(impl)[0].url, '/v1/dashboard/paired-chats');
	assert.equal(result.paired_chats.length, 1);
	assert.equal(result.paired_chats[0].principal_display, '@ada_l');
});

test('fetchPairedChats raises ReposAuthError on 401', async () => {
	const impl = fakeFetch(401, { detail: 'unauthenticated' });
	await assert.rejects(() => fetchPairedChats(impl), ReposAuthError);
});

// #1464 — revoke deletes the ChannelRoute outright (kills the principal,
// not #1459's repo-unpin); a DELETE with no body.
test('revokePairedChat DELETEs the route by id, escaped as a path segment', async () => {
	const impl = fakeFetch(200, { ok: true });
	await revokePairedChat('chan/../x', impl);
	assert.equal(calls(impl)[0].url, '/v1/dashboard/paired-chats/chan%2F..%2Fx');
	assert.equal(calls(impl)[0].init?.method, 'DELETE');
});

test('revokePairedChat raises ReposAuthError on 401', async () => {
	const impl = fakeFetch(401, { detail: 'unauthenticated' });
	await assert.rejects(() => revokePairedChat('chan_1', impl), ReposAuthError);
});

test('revokePairedChat raises on a non-401 error status (e.g. a route owned by another account)', async () => {
	const impl = fakeFetch(404, { detail: 'paired chat not found' });
	await assert.rejects(() => revokePairedChat('chan_1', impl), /revoke failed: 404/);
});
