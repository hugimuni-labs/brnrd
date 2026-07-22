import assert from 'node:assert/strict';
import test from 'node:test';

import {
	ConnectAuthError,
	approveConnect,
	canApprove,
	fetchConnectContext,
	statusNotice,
	type ConnectContext
} from './connect.ts';

const REPO = { id: 'repo_1', repo_full_name: 'Gurio/laptop' };

function ctx(overrides: Partial<ConnectContext> = {}): ConnectContext {
	return { code: 'BR-123', status: 'pending', repos: [REPO], ...overrides };
}

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

test('fetchConnectContext hits the JSON endpoint with the session cookie', async () => {
	const impl = fakeFetch(200, ctx());
	const context = await fetchConnectContext('BR-123', impl);
	assert.equal(context.status, 'pending');
	assert.deepEqual(context.repos, [REPO]);
	const calls = (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
	assert.equal(calls[0].url, '/v1/connect/BR-123');
	assert.equal(calls[0].init?.credentials, 'include');
});

test('fetchConnectContext escapes the code as a path segment', async () => {
	const impl = fakeFetch(200, ctx());
	await fetchConnectContext('BR/../x', impl);
	const calls = (impl as unknown as { calls: { url: string }[] }).calls;
	assert.equal(calls[0].url, '/v1/connect/BR%2F..%2Fx');
});

test('fetchConnectContext raises the auth error on 401', async () => {
	await assert.rejects(
		() => fetchConnectContext('BR-123', fakeFetch(401, { detail: 'unauthenticated' })),
		ConnectAuthError
	);
});

test('approveConnect posts the repo id and returns the backend notice', async () => {
	const impl = fakeFetch(200, {
		ok: true,
		notice: 'Your daemon is connected. You can return to your terminal.',
		telegram: { pair_code: 'TG-1', instructions: 'send /start TG-1', deep_link: null }
	});
	const result = await approveConnect('BR-123', 'repo_1', impl);
	assert.equal(result.ok, true);
	assert.match(result.notice, /daemon is connected/);
	assert.equal(result.telegram?.pair_code, 'TG-1');
	const calls = (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
	assert.equal(calls[0].init?.method, 'POST');
	assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { repo_id: 'repo_1' });
});

test('approveConnect surfaces backend rejections without throwing', async () => {
	// 409/410/404 from approve_core carry {ok: false, notice} — render, not crash.
	const result = await approveConnect(
		'BR-123',
		'repo_1',
		fakeFetch(409, { ok: false, notice: 'pair code already used' })
	);
	assert.equal(result.ok, false);
	assert.equal(result.notice, 'pair code already used');
	assert.equal(result.telegram, null);
});

test('approveConnect raises the auth error on 401', async () => {
	await assert.rejects(
		() => approveConnect('BR-123', 'repo_1', fakeFetch(401, { detail: 'unauthenticated' })),
		ConnectAuthError
	);
});

test('canApprove needs a live code and at least one repo', () => {
	assert.equal(canApprove(ctx()), true);
	assert.equal(canApprove(ctx({ status: 'approved' })), true);
	assert.equal(canApprove(ctx({ status: 'expired' })), false);
	assert.equal(canApprove(ctx({ status: 'consumed' })), false);
	assert.equal(canApprove(ctx({ status: 'unknown' })), false);
	assert.equal(canApprove(ctx({ repos: [] })), false);
});

test('statusNotice names every terminal state and stays silent when live', () => {
	assert.equal(statusNotice(ctx()), null);
	assert.match(String(statusNotice(ctx({ status: 'unknown' }))), /unknown/);
	assert.match(String(statusNotice(ctx({ status: 'expired' }))), /expired/);
	assert.match(String(statusNotice(ctx({ status: 'consumed' }))), /already used/);
	assert.match(String(statusNotice(ctx({ repos: [] }))), /No repos connected yet/);
});
