import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { WithheldLane } from './withheld.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'WithheldNotice.svelte');
const generated = join(here, '.withheldNotice.generated.mjs');

// Same rendering dance as pricing.test.ts / legalNotice.test.ts: compile the
// real component to a server target and render it with props, rather than
// string-matching the source — a sentence that only goes missing in the
// rendered HTML (a broken `{#if}`, a swallowed class) still fails this test.
// `$app/paths`'s `resolve` only exists inside a SvelteKit build, so it is
// swapped for the identity function it is at this app's root base path.
// `ConsentPopover` is stubbed to a no-op (same dance as
// BackchannelQueue.test.ts's WithheldNotice stub): it doesn't compile
// standalone outside a bundler's `.svelte` resolution, and its own markup
// is covered by ConsentPopover.test.ts — this file only needs to know
// whether WithheldNotice decided to mount it.
async function renderNotice(withheld: WithheldLane): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'WithheldNotice'
	});
	const runnable = compiled.js.code
		.replace(
			/import\s+ConsentPopover\s+from\s*'\.\/ConsentPopover\.svelte';/,
			'const ConsentPopover = () => {};'
		)
		.replace(/'\.\/publishScope'/g, "'./publishScope.ts'")
		.replace(/'\.\/consentGap'/g, "'./consentGap.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { withheld } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test("bare withheld lane (neither list) keeps today's sentence", async () => {
	const html = await renderNotice({ lane: 'corpus' });
	ok(html.includes('paused — no publish scope'));
});

test('an unrecorded repo is named, and the reopening act is a /repos link', async () => {
	const html = await renderNotice({ lane: 'corpus', unrecorded: ['Gurio/BeCenter'] });
	ok(html.includes('Gurio/BeCenter'), 'the withholding repo must be named, not just "paused"');
	ok(html.includes('never recorded a publish scope'));
	ok(html.includes('href="/repos"'), 'the reopening act must be an actual link to /repos');
	ok(!html.includes('paused — no publish scope'), 'the bare sentence must not also render');
});

test('an opted-out repo is named as having chosen to publish nothing', async () => {
	const html = await renderNotice({ lane: 'corpus', opted_out: ['Gurio/other-repo'] });
	ok(html.includes('Gurio/other-repo'));
	ok(html.includes('chose to publish nothing'));
	ok(!html.includes('never recorded'), 'an opted-out repo did not fail to answer — it answered no');
});

test('both lists present renders both clauses, unrecorded first', async () => {
	const html = await renderNotice({
		lane: 'corpus',
		unrecorded: ['Gurio/BeCenter'],
		opted_out: ['Gurio/other-repo']
	});
	ok(html.includes('Gurio/BeCenter'));
	ok(html.includes('Gurio/other-repo'));
	ok(html.includes('never recorded a publish scope'));
	ok(html.includes('chose to publish nothing'));
	ok(html.includes('href="/repos"'));
	const unrecordedAt = html.indexOf('Gurio/BeCenter');
	const optedOutAt = html.indexOf('Gurio/other-repo');
	ok(
		unrecordedAt !== -1 && optedOutAt !== -1 && unrecordedAt < optedOutAt,
		'unrecorded clause must come first'
	);
});

test('two unrecorded repos are both named, joined in natural language', async () => {
	const html = await renderNotice({
		lane: 'corpus',
		unrecorded: ['Gurio/BeCenter', 'Gurio/other-repo']
	});
	ok(html.includes('Gurio/BeCenter'));
	ok(html.includes('Gurio/other-repo'));
	equal(html.includes('Gurio/BeCenter and Gurio/other-repo'), true);
});

// ── the in-place act (id-bearing payload) ──────────────────────────────

test('an id-bearing gap mounts the fix-it-here trigger and the dialog', async () => {
	const html = await renderNotice({
		lane: 'corpus',
		unrecorded: ['Gurio/BeCenter'],
		unrecorded_ids: ['repo-1']
	});
	ok(html.includes('Or fix it here.'), 'the in-place trigger must render when a real id is known');
});

test('a name with no id twin gets no in-place trigger — only the /repos link', async () => {
	const html = await renderNotice({ lane: 'corpus', unrecorded: ['Gurio/legacy'] });
	ok(!html.includes('Or fix it here.'), 'no id ⇒ no dead in-place button');
	ok(html.includes('href="/repos"'));
});

test('the bare "no publish scope" state (no names at all) gets no trigger either', async () => {
	const html = await renderNotice({ lane: 'corpus' });
	ok(!html.includes('Or fix it here.'));
});
