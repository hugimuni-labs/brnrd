import assert from 'node:assert/strict';
import test from 'node:test';

import { connectRepo, setPublishLayers } from './repos.ts';

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
