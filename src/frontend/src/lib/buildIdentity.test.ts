import { equal, ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import { buildIdentityView, type BuildIdentityView } from './buildIdentity.ts';
import { GITHUB_REPO } from './publicStats.ts';

const NOW = Date.parse('2026-09-01T12:00:00Z');

// --- buildIdentityView: pure formatting over a fetched BuildVersion -------

test('null version (fetch failed, or endpoint unreachable) renders nothing', () => {
	equal(buildIdentityView(null, NOW), null);
});

test('no build_info.txt stamped (local/dev install): both fields null renders nothing', () => {
	equal(
		buildIdentityView({ commit: null, built_at: null, started_at: '2026-09-01T11:00:00Z' }, NOW),
		null
	);
});

test('a real commit renders short and linked to the forge commit page', () => {
	const view = buildIdentityView(
		{ commit: 'c5a1ce2a715421454d8d3924ff28715d49981f62', built_at: null, started_at: null },
		NOW
	);
	ok(view);
	equal(view.commitShort, 'c5a1ce2a');
	equal(
		view.commitUrl,
		`https://github.com/${GITHUB_REPO}/commit/c5a1ce2a715421454d8d3924ff28715d49981f62`
	);
	// No built_at was sent — the age half is absent, not guessed from
	// something else (e.g. started_at).
	equal(view.builtAgo, null);
});

test('built_at alone (a commit-less stamp, or a source the backend does not trust) still renders an age', () => {
	// Kept inside ageLabel's under-an-hour relative window (#1256) — past
	// that it switches to a viewer-local clock reading, which would make
	// this assertion depend on the test machine's timezone.
	const view = buildIdentityView(
		{ commit: null, built_at: '2026-09-01T11:30:00Z', started_at: null },
		NOW
	);
	ok(view);
	equal(view.commitShort, null);
	equal(view.commitUrl, null);
	equal(view.builtAgo, '30m ago');
});

test('both fields present render both, through the dashboard-wide age grammar', () => {
	const view = buildIdentityView(
		{
			commit: 'abc12340deadbeef',
			built_at: '2026-09-01T11:30:00Z',
			started_at: '2026-09-01T11:31:00Z'
		},
		NOW
	);
	ok(view);
	equal(view.commitShort, 'abc12340');
	equal(view.builtAgo, '30m ago');
});

// Mutation check: an implementation that read `commit.slice(0, 7)` (7, the
// common short-sha length elsewhere) instead of 8 would still pass every
// other case above but silently truncate — that only 8 characters survive
// is asserted on its own so a slice-length regression goes red here first.
test('the short commit is exactly 8 characters, not 7 or the full sha', () => {
	const view = buildIdentityView(
		{ commit: 'c5a1ce2a715421454d8d3924ff28715d49981f62', built_at: null, started_at: null },
		NOW
	);
	equal(view?.commitShort?.length, 8);
});

// --- BuildIdentity.svelte: the component's own render path ----------------
//
// Same server-side render dance as WinkWordmark.test.ts / RunBlock.test.ts:
// compile with `generate: 'server'`, drop the extension the compiler strips
// off relative specifiers so Node's ESM resolver can find them, and assert
// on the produced markup. Named `buildIdentity.test.ts` rather than a
// sibling `BuildIdentity.test.ts` — the two would collide on a
// case-insensitive filesystem (macOS/Windows checkouts), silently
// overwriting whichever wrote second; one file for the module and its
// component avoids relying on a case-sensitive filesystem existing.
const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'BuildIdentity.svelte');
const generated = join(here, '.buildIdentity.generated.mjs');

async function renderBuildIdentity(view: BuildIdentityView | null): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'BuildIdentity' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props: { view } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

// Svelte 5's SSR output for a false `{#if}` is a pair of hydration comment
// anchors, not the empty string (see MessengerDoorsPanel.test.ts) — assert
// on the absence of real content instead of a byte-exact empty body.
test('a null view (nothing honest to show — local/dev install, or the fetch failed) renders nothing', async () => {
	const body = await renderBuildIdentity(null);
	ok(!body.includes('built '), 'no built-age clause renders');
	ok(!body.includes('<a '), 'no commit link renders');
	ok(!body.includes('·'), 'no separator renders');
});

test('a commit renders short and linked to the forge commit page', async () => {
	const body = await renderBuildIdentity(
		buildIdentityView(
			{ commit: 'c5a1ce2a715421454d8d3924ff28715d49981f62', built_at: null, started_at: null },
			NOW
		)
	);
	ok(body.includes('c5a1ce2a'), 'the short commit renders');
	ok(
		body.includes(
			'href="https://github.com/hugimuni-labs/brnrd/commit/c5a1ce2a715421454d8d3924ff28715d49981f62"'
		),
		'linked to the forge commit page'
	);
	// No built_at, so no separator and no "built …" clause.
	ok(!body.includes('·'), 'no separator renders when only one half is present');
	ok(!body.includes('built '), 'no built-age clause renders when built_at is absent');
});

test('built_at alone renders an age with no link and no separator', async () => {
	const body = await renderBuildIdentity(
		buildIdentityView({ commit: null, built_at: '2026-09-01T11:30:00Z', started_at: null }, NOW)
	);
	ok(body.includes('built 30m ago'), 'the age renders');
	ok(!body.includes('<a '), 'no link renders with no commit to link to');
	ok(!body.includes('·'), 'no separator renders when only one half is present');
});

test('both present render commit, separator, and age together', async () => {
	const body = await renderBuildIdentity(
		buildIdentityView(
			{
				commit: 'c5a1ce2a715421454d8d3924ff28715d49981f62',
				built_at: '2026-09-01T11:30:00Z',
				started_at: null
			},
			NOW
		)
	);
	ok(body.includes('c5a1ce2a'));
	ok(body.includes('built 30m ago'));
	ok(body.includes('·'), 'the separator renders between the two halves');
});
