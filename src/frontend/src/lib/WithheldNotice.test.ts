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
async function renderNotice(withheld: WithheldLane): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'WithheldNotice'
	});
	const runnable = compiled.js.code
		.replace(/'\.\/publishScope'/g, "'./publishScope.ts'")
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
