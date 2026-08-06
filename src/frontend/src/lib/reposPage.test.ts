import { ok } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Source-level, deliberately — same call as ColdStart.test.ts's
// SIGNED_IN_ROUTES check: `/repos/+page.svelte` reads `page`, `onMount`, and
// `localStorage`, none of which the SSR-compile-and-render harness the other
// component tests use can stub cheaply, and the two claims here are about
// literal markup (a link's text, a form's method/action), not derived state.
const here = dirname(fileURLToPath(import.meta.url));
const reposPagePath = join(here, '..', 'routes', 'repos', '+page.svelte');

function source(): string {
	return readFileSync(reposPagePath, 'utf8');
}

// THE FOURTH FACT (`brr/one-sequence-two-surfaces`): three stat tiles read
// three GitHub-supplied facts and offer no rung for "is a brnrd CLI on this
// machine" — the "connect this repository" section started at `cd <repo>`,
// presuming a binary that may not exist. This page stays a management
// surface rather than growing a second onboarding ladder
// (design-onboarding-ladder.md Direction A keeps the install rung on the
// dashboard), so the fix is an explicit defer by name, not a duplicated
// install command.
test('the "connect this repository" section defers to the dashboard cold-start block by name', () => {
	const src = source();
	// Anchored inside the "connect this repository" section specifically —
	// both `cold-start` (a pre-existing, unrelated comment at "the same
	// section the cold-start block's own...") and a bare `dashboard` (the
	// page's own header nav link) already existed in this file, so a loose
	// substring check on either alone would pass whether or not the actual
	// defer sentence was ever written. This regex pins the sentence itself.
	ok(
		/starts one rung earlier, with the install command/.test(src),
		'names, by content, that the dashboard ladder carries the install rung'
	);
	const connectSection = src.match(/id="connect-heading"[\s\S]{0,600}/);
	ok(connectSection, 'the "connect this repository" heading exists');
	ok(
		/dashboard/.test(connectSection![0]) && /href=\{resolve\('\/'\)\}/.test(connectSection![0]),
		'the defer sentence, naming the dashboard, sits inside that same section'
	);
	// One constant: this page must not retype the install command that
	// already lives in ColdStart.svelte — only defer to it.
	ok(!src.includes('npm install -g brnrd'), 'does not retype the install command');
});

// The re-sync control (#1084's escape hatch): `POST /api/github/sync` exists,
// is covered by backend tests, and had no frontend caller at all (`grep -rn
// "github/sync" src --include='*.svelte'` found nothing before this branch).
// A plain form post, not a `fetch` call — the endpoint answers with a
// redirect, the same shape the GitHub Setup URL return already produces.
test('a plain form posts to the github sync endpoint', () => {
	const src = source();
	ok(
		/<form\s+method="POST"\s+action="\/api\/github\/sync">/.test(src),
		'a real <form> POSTs to the sync endpoint, not a fetch()'
	);
});

test('the sync control is not labelled "sync"', () => {
	const src = source();
	const match = src.match(/<form method="POST" action="\/api\/github\/sync">([\s\S]*?)<\/form>/);
	ok(match, 'the sync form exists');
	const body = match![1];
	ok(!/>\s*sync\s*</i.test(body), 'the visible label is not the bare word "sync"');
	ok(body.toLowerCase().includes('github'), 'the label names what it rechecks');
});

// Offering a control that can only ever fail closed (no App credentials
// configured server-side) is worse than no control — gate on the flag the
// backend already serves for exactly this and that no component read yet.
test('the sync control is gated on github_sync_configured', () => {
	const src = source();
	const guardThenForm =
		/\{#if data\.github_sync_configured\}[\s\S]{0,1200}?action="\/api\/github\/sync"/;
	ok(guardThenForm.test(src), 'the sync form sits inside a github_sync_configured guard');
});

// THE DEAD END WITH NOWHERE TO REPORT BACK, the /repos half
// (`connectPage.test.ts` owns the link that sends readers here): a pending
// daemon pairing that named no repo of its own used to strand the reader
// on this page once they connected one — nothing pointed back, and the
// pairing's server-side TTL kept ticking underneath them the whole time.
test('a validated next= query param is read, and only a /connect/<code> shape is trusted', () => {
	const src = source();
	ok(
		/searchParams\.get\('next'\)/.test(src),
		'reads next= off the current URL, the same param /login already uses'
	);
	ok(
		/\/\^\\\/connect\\\/\(\[A-Za-z0-9-\]\{1,40\}\)\$\//.test(src),
		'validates next= against its own path shape before trusting it as a nav target — a query param is reader input, even for a client-side goto'
	);
});

test('connecting a repo through the manual form returns to a pending pairing on success', () => {
	const src = source();
	const manualFn = src.match(/function connectManual\([\s\S]{0,700}?\n\t\}/);
	ok(manualFn, 'connectManual exists');
	ok(
		/returnOnSuccess:\s*true/.test(manualFn![0]),
		'the manual connect path opts into the post-success redirect — the one browser-side connect action left after 603d27fc retired the per-repo "enable" buttons'
	);
	const runActionStart = src.indexOf('async function runAction(');
	ok(runActionStart >= 0, 'runAction exists');
	const runActionFn = src.slice(runActionStart, runActionStart + 1200);
	ok(
		/returnOnSuccess && returnCode\)\s*\{\s*\n\s*await goto\(resolve\('\/connect\/\[code\]', \{ code: returnCode \}\)\)/.test(
			runActionFn
		),
		'runAction actually navigates to returnTo on a successful, opted-in action'
	);
});
