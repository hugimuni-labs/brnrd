import assert from 'node:assert/strict';
import test from 'node:test';

import {
	BenchAuthError,
	benchAgeLabel,
	benchFileHref,
	fetchBench,
	fetchBenchFile
} from './bench.ts';

function fetchStub(status: number, body: unknown): typeof fetch {
	return (async () =>
		new Response(JSON.stringify(body), {
			status,
			headers: { 'content-type': 'application/json' }
		})) as typeof fetch;
}

test('fetchBench returns the files array on 200', async () => {
	const files = [
		{
			repo: 'Gurio__brr',
			place: 'src/brr/daemon.py',
			commit: 'abc1234',
			question: 'why retry',
			made_at: '2026-09-14T10:00:00Z',
			marks: [],
			path: 'Gurio__brr/src/brr/daemon.py/abc1234'
		}
	];
	const result = await fetchBench(fetchStub(200, { files }));
	assert.deepEqual(result.files, files);
});

test('fetchBench throws BenchAuthError on 401', async () => {
	await assert.rejects(() => fetchBench(fetchStub(401, {})), BenchAuthError);
});

test('fetchBench throws a plain Error on any other failure status', async () => {
	await assert.rejects(() => fetchBench(fetchStub(500, {})), /500/);
});

test('fetchBenchFile requests the encoded repo/place/commit path', async () => {
	let requestedUrl = '';
	const fetchImpl = (async (input: RequestInfo | URL) => {
		requestedUrl = String(input);
		return new Response(JSON.stringify({ body: 'the fold\n' }), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		});
	}) as typeof fetch;

	await fetchBenchFile('Gurio__brr', 'src/brr/daemon.py', 'abc1234', fetchImpl);

	assert.equal(requestedUrl, '/v1/dashboard/bench/Gurio__brr/src/brr/daemon.py/abc1234');
});

test('fetchBenchFile returns null on 404', async () => {
	const result = await fetchBenchFile('repo', 'place', 'nope', fetchStub(404, {}));
	assert.equal(result, null);
});

test('fetchBenchFile throws BenchAuthError on 401', async () => {
	await assert.rejects(
		() => fetchBenchFile('repo', 'place', 'commit', fetchStub(401, {})),
		BenchAuthError
	);
});

test('benchFileHref keeps place nested and encodes every segment', () => {
	assert.equal(
		benchFileHref('Gurio__brr', 'src/brr/daemon.py', 'abc 1234'),
		'/bench/Gurio__brr/src/brr/daemon.py/abc%201234'
	);
});

test('benchAgeLabel renders a short unit for each band, and empty for absent/unparseable', () => {
	const now = new Date('2026-09-14T12:00:00Z');
	assert.equal(benchAgeLabel('2026-09-14T11:59:30Z', now), '30s');
	assert.equal(benchAgeLabel('2026-09-14T11:30:00Z', now), '30m');
	assert.equal(benchAgeLabel('2026-09-13T12:00:00Z', now), '24h');
	assert.equal(benchAgeLabel('2026-09-10T12:00:00Z', now), '4d');
	assert.equal(benchAgeLabel(null, now), '');
	assert.equal(benchAgeLabel('not a date', now), '');
});
