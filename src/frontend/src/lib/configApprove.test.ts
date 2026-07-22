import assert from 'node:assert/strict';
import test from 'node:test';

import {
	ConfigApproveAuthError,
	canDecide,
	decideConfigApproveRequest,
	fetchConfigApproveRequest,
	statusNotice,
	type ConfigApproveRequest
} from './configApprove.ts';

function request(overrides: Partial<ConfigApproveRequest> = {}): ConfigApproveRequest {
	return {
		id: 'ccr-1',
		repo_label: 'Gurio/brr',
		config_key: 'spawn.max_concurrent',
		current_value: '4',
		requested_value: '8',
		reason: 'need capacity',
		status: 'pending',
		expires_at: null,
		...overrides
	};
}

function fakeFetch(status: number, body: unknown): typeof fetch {
	const calls: { url: string; init?: RequestInit }[] = [];
	const impl = (async (url: string, init?: RequestInit) => {
		calls.push({ url, init });
		return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
	}) as unknown as typeof fetch;
	(impl as unknown as { calls: typeof calls }).calls = calls;
	return impl;
}

test('config approval context uses the session-authenticated JSON endpoint', async () => {
	const impl = fakeFetch(200, request());
	const context = await fetchConfigApproveRequest('ccr/../1', impl);
	assert.equal(context.config_key, 'spawn.max_concurrent');
	const calls = (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
	assert.equal(calls[0].url, '/v1/config-approve/ccr%2F..%2F1');
	assert.equal(calls[0].init?.credentials, 'include');
});

test('config approval forwards explicit decisions and exposes success notices', async () => {
	const impl = fakeFetch(200, {
		ok: true,
		notice: 'Approved.',
		request: request({ status: 'approved' })
	});
	const result = await decideConfigApproveRequest('ccr-1', 'approve', impl);
	assert.equal(result.ok, true);
	assert.equal(result.request?.status, 'approved');
	const calls = (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
	assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { decision: 'approve' });
});

test('config approval keeps backend rejection notices visible', async () => {
	const result = await decideConfigApproveRequest(
		'ccr-1',
		'reject',
		fakeFetch(403, { ok: false, notice: 'not your config-change request' })
	);
	assert.equal(result.ok, false);
	assert.equal(result.notice, 'not your config-change request');
});

test('config approval authentication is a separate state', async () => {
	await assert.rejects(
		() => fetchConfigApproveRequest('ccr-1', fakeFetch(401, {})),
		ConfigApproveAuthError
	);
	await assert.rejects(
		() => decideConfigApproveRequest('ccr-1', 'approve', fakeFetch(401, {})),
		ConfigApproveAuthError
	);
});

test('only live pending requests expose action buttons', () => {
	assert.equal(canDecide(request()), true);
	assert.equal(canDecide(request({ status: 'approved' })), false);
	assert.equal(canDecide(request({ status: 'expired' })), false);
	assert.equal(canDecide(request({ expires_at: '2000-01-01T00:00:00+00:00' })), false);
	assert.match(String(statusNotice(request({ status: 'rejected' }))), /Already rejected/);
	assert.match(
		String(statusNotice(request({ expires_at: '2000-01-01T00:00:00+00:00' }))),
		/expired/
	);
});
