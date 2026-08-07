import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'MarkerNotice.svelte');
const generated = join(here, '.markerNotice.generated.mjs');

// Same rendering dance as WithheldNotice.test.ts / PublishConsentNotice.test.ts:
// compile the real component to a server target and render it with props,
// rather than string-matching the source.
async function renderNotice(props: {
	status: 'permission-missing' | 'not-a-collaborator' | 'check-unavailable' | 'unknown' | null;
	botLogin?: string;
	repoFullName?: string;
	collaborator?: boolean | null;
	checkedLabel?: string;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'MarkerNotice'
	});
	writeFileSync(generated, compiled.js.code);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('neither notice renders nothing visible', async () => {
	const html = await renderNotice({ status: null });
	ok(!html.includes('marker'));
	ok(!html.includes('not a collaborator'));
});

// Re-registered 2026-08-04: this used to read as a remediation notice for a
// broken summons path. It is an optional upgrade — the App-native `brnrd`
// label already summons the resident regardless — so the copy must say what
// the invite *adds* (assignment / review requests / @ autocomplete), not
// imply anything is currently unreachable.
test('a determined absence renders the optional-upgrade framing, naming the effective login', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: 'brnrd-bot'
	});
	ok(html.includes("brnrd-bot isn't a collaborator"));
	ok(html.includes('optional, not required'));
	ok(html.includes('the brnrd label already summons it'));
	ok(html.includes('assignment, review requests, and @ autocomplete'));
	ok(html.includes('Settings → Collaborators'));
	ok(!html.includes("won't reach the resident"), 'must not overstate as a broken summons path');
});

test('unknown renders as unknown rather than guessing yes or no', async () => {
	const html = await renderNotice({ status: 'unknown' });
	ok(html.includes('collaborator status unknown'));
	ok(!html.includes('not a collaborator'));
});

// Rewritten 2026-08-05 (#1141 — "the lamp that blamed the app"): the old
// copy told the reader to grant `Administration: read` in the App's
// repository permissions. Wrong principal (this check never used the App's
// grant at all), wrong permission (only `Metadata: read` is needed, and the
// App already holds it), and not something any end user can act on in any
// case — the copy must name the fact and that it's the operator's to fix,
// never send the reader chasing a permission that was never the gate.
test('permission-missing names the operator, never an App permission to grant', async () => {
	const html = await renderNotice({ status: 'permission-missing' });
	ok(html.includes('collaborator status unavailable'));
	ok(html.includes('brnrd operator'));
	ok(!html.includes('Administration: read'));
	ok(!html.includes('GitHub App lacks the grant'));
	ok(!html.includes('403 Forbidden'));
	ok(!html.includes('developer.mozilla.org'));
});

test('permission-missing renders at the same quiet weight as an optional notice, not urgent amber', async () => {
	const html = await renderNotice({ status: 'permission-missing' });
	ok(!html.includes('text-amber-400'));
	ok(html.includes('text-ink-quiet'));
});

test('not-a-collaborator renders at neutral weight, not urgent amber — it is optional, not a fault', async () => {
	const html = await renderNotice({ status: 'not-a-collaborator', botLogin: 'brnrd-bot' });
	ok(!html.includes('text-amber-400'));
	ok(html.includes('text-ink-quiet'));
});

// A genuine, actionable failure (transient or a real config gap) keeps the
// urgent tone — only the two operator-scope/optional states above were
// misweighted.
test('check-unavailable keeps the urgent amber weight', async () => {
	const html = await renderNotice({ status: 'check-unavailable' });
	ok(html.includes('text-amber-400'));
});

// #1141 §4 — the satisfied state. Before this fix, `status` was `null` for
// both "invited and accepted" and "never checked": byte-identical to a
// reader. `collaborator` disambiguates.
test('a confirmed collaborator renders the lit line, quiet and non-amber', async () => {
	const html = await renderNotice({
		status: null,
		collaborator: true,
		botLogin: 'brnrd-bot',
		checkedLabel: '3m ago'
	});
	ok(html.includes('marker'));
	ok(html.includes('brnrd-bot is a collaborator'));
	ok(html.includes('checked 3m ago'));
	ok(!html.includes('text-amber-400'));
});

test('never checked (status null, collaborator null) renders nothing, distinct from the lit line', async () => {
	const html = await renderNotice({ status: null, collaborator: null });
	ok(!html.includes('marker'));
	ok(!html.includes('is a collaborator'));
});

test('check-unavailable renders a retry remedy', async () => {
	const html = await renderNotice({ status: 'check-unavailable' });
	ok(html.includes('collaborator check unavailable'));
	ok(html.includes('try again later'));
});

// #885 additions: click-to-copy bot handle + a link to the repo's GitHub
// collaborators page, both gated on the `not-a-collaborator` class.

test('a determined absence with a bot login renders the copy-handle control', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: 'brnrd-bot',
		repoFullName: ''
	});
	ok(html.includes('copy @brnrd-bot'));
});

test('a determined absence with a repo name renders the collaborators-page link', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: '',
		repoFullName: 'Gurio/brr'
	});
	ok(html.includes('open collaborators page'));
	ok(html.includes('https://github.com/Gurio/brr/settings/access'));
});

test('an empty botLogin/repoFullName renders neither addition, even with a firing status', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: '',
		repoFullName: ''
	});
	ok(!html.includes('copy @'));
	ok(!html.includes('open collaborators page'));
});

test('a null status renders neither addition, even when bot login and repo name are given', async () => {
	const html = await renderNotice({
		status: null,
		botLogin: 'brnrd-bot',
		repoFullName: 'Gurio/brr'
	});
	ok(!html.includes('copy @'));
	ok(!html.includes('open collaborators page'));
});
