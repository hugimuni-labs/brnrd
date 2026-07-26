import assert from 'node:assert/strict';
import test from 'node:test';

import {
	DOC_HOSTED,
	DOC_TOS,
	acceptDocument,
	fetchTermsStatus,
	safeNext,
	type TermsStatus
} from './terms.ts';

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

function callsOf(impl: typeof fetch): { url: string; init?: RequestInit }[] {
	return (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
}

const STATUS: TermsStatus = {
	authenticated: true,
	documents: {
		[DOC_TOS]: {
			version: '2026-07-24',
			sha256: 'a'.repeat(64),
			accept_url: '/terms',
			needs_accept: true,
			accepted_at: null,
			accepted_sha256: null
		},
		[DOC_HOSTED]: {
			version: '2026-07-08',
			sha256: 'b'.repeat(64),
			accept_url: '/beta-hosted-execution',
			needs_accept: false,
			accepted_at: '2026-07-20T10:00:00+00:00',
			accepted_sha256: ''
		}
	}
};

test('terms status is read per document, not as one flat verdict', async () => {
	const impl = fakeFetch(200, STATUS);
	const status = await fetchTermsStatus(impl);
	assert.equal(status.documents[DOC_TOS].needs_accept, true);
	assert.equal(status.documents[DOC_HOSTED].needs_accept, false);
	assert.equal(callsOf(impl)[0].url, '/v1/dashboard/terms-status');
});

test('a failed status fetch throws rather than reporting "nothing owed"', async () => {
	// The dangerous default: a network blip that renders as "you are all set"
	// would hide an outstanding acceptance behind a green page.
	await assert.rejects(
		() => fetchTermsStatus(fakeFetch(503, {})),
		/terms-status fetch failed: 503/
	);
});

test('accepting names the document it accepts', async () => {
	const impl = fakeFetch(200, { ok: true });
	const outcome = await acceptDocument(DOC_TOS, impl);
	assert.deepEqual(outcome, { ok: true });
	// #569: a checkbox may only record acceptance of the words beside it, so
	// the document key travels with every accept — never implied by the page.
	assert.deepEqual(JSON.parse(String(callsOf(impl)[0].init?.body)), {
		document: DOC_TOS,
		accept_terms: 'yes'
	});
});

test('an unauthenticated accept is distinguishable from a rejected one', async () => {
	const unauth = await acceptDocument(DOC_TOS, fakeFetch(401, { detail: 'unauthenticated' }));
	assert.equal(unauth.ok, false);
	assert.equal(unauth.ok === false && unauth.unauthenticated, true);

	const refused = await acceptDocument(DOC_TOS, fakeFetch(400, { ok: false, notice: 'tick it' }));
	assert.equal(refused.ok, false);
	assert.equal(refused.ok === false && refused.unauthenticated, false);
	assert.equal(refused.ok === false && refused.notice, 'tick it');
});

test('a 200 that is not ok:true is still a failure', async () => {
	const outcome = await acceptDocument(DOC_TOS, fakeFetch(200, { ok: false, notice: 'nope' }));
	assert.equal(outcome.ok, false);
});

test('safeNext refuses protocol-relative and absolute destinations', () => {
	assert.equal(safeNext('/connect/BR-123'), '/connect/BR-123');
	assert.equal(safeNext('//evil.example'), '/');
	assert.equal(safeNext('https://evil.example'), '/');
	assert.equal(safeNext(null), '/');
	assert.equal(safeNext(''), '/');
});

test('safeNext refuses the backslash form of an off-site authority', () => {
	// This module is the sink the guard exists for: the gate hands every
	// un-accepted user a `next=` parameter and TermsGate calls
	// window.location.assign on it. `/\host` passes a naive startsWith('/')
	// check and the browser still resolves it off-site — asserted here rather
	// than assumed, so a future simplification of safeNext cannot quietly
	// reopen it.
	assert.equal(new URL('/\\evil.example', 'https://brnrd.dev').origin, 'https://evil.example');
	assert.equal(safeNext('/\\evil.example'), '/');
	assert.equal(safeNext('/\\\\evil.example'), '/');
	// A backslash anywhere but the authority position is an ordinary path
	// character and must survive.
	assert.equal(safeNext('/repos/a\\b'), '/repos/a\\b');
});
