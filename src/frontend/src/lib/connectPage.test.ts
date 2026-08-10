import { ok } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Source-level, deliberately — same call as reposPage.test.ts: this page
// reads `page`, `onMount`, and route params, none of which the
// SSR-compile-and-render harness the other component tests use can stub
// cheaply, and the claim here is about literal markup (a link's `href`),
// not derived state.
const here = dirname(fileURLToPath(import.meta.url));
const connectPagePath = join(here, '..', 'routes', 'connect', '[code]', '+page.svelte');

function source(): string {
	return readFileSync(connectPagePath, 'utf8');
}

// THE DEAD END WITH NOWHERE TO REPORT BACK (live 2026-08-06): the
// needsRepoEnable branch's "connect a repository" link used to send the
// reader to /repos with no memory of the pairing that sent them — they'd
// connect a repo there and be stranded, while the pair code's server-side
// TTL kept ticking underneath. The link must carry the pairing's own code
// back as `next=`, so /repos (reposPage.test.ts owns that half) can return
// them once a repo connects.
test('the "connect a repository" dead-end link carries the pairing back as next=', () => {
	const src = source();
	const needsRepoEnableBlock = src.match(
		/\{#if needsRepoEnable\(context\)\}[\s\S]{0,400}?\{\/if\}/
	);
	ok(needsRepoEnableBlock, 'the needsRepoEnable branch exists');
	const block = needsRepoEnableBlock![0];
	ok(
		/href=\{resolve\(`\/repos\?next=/.test(block),
		'the link points at /repos with a next= query param, routed through resolve() for typed navigation'
	);
	ok(
		/encodeURIComponent\(connectNextUrl\(code, hash\)\)/.test(block),
		'next= carries this exact pairing code back, not a bare /repos'
	);
});

// A-1: the same round trip has to carry the *approval proof* home too. The
// proof rides the URL fragment, and a detour that rebuilds the path from
// `code` alone drops it — the reader returns to a live code they can no
// longer approve. `connectNextUrl` is the one spelling of "this pairing,
// with everything it needs", so the two detours off this page (sign-in and
// connect-a-repo) both go through it.
test('every detour off the approval page rebuilds the link through connectNextUrl', () => {
	const src = source();
	ok(/href=\{loginUrlForConnect\(code, hash\)\}/.test(src), 'the sign-in link carries the hash');
	ok(
		/encodeURIComponent\(connectNextUrl\(code, hash\)\)/.test(src),
		'the connect-a-repo detour carries the hash'
	);
	ok(
		!/`\/connect\/\$\{code\}`/.test(src),
		'no hand-rolled /connect/<code> path that would silently drop the fragment'
	);
});
