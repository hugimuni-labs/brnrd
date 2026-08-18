import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { WithheldLane } from './withheld.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ConsentPopover.svelte');
const generated = join(here, '.consentPopoverComponent.generated.mjs');

// Same rendering dance as WithheldNotice.test.ts: compile the real component
// to a server target and render it with props. This only reaches the markup
// a fresh `<dialog>` renders closed (Svelte SSR does not run browser-only
// APIs like `showModal`) — the open/enable/error interaction is exercised
// through consentGap.ts's pure-logic tests instead; there is no DOM harness
// in this repo to simulate a click.
async function renderPopover(withheld: WithheldLane): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'ConsentPopover'
	});
	const runnable = compiled.js.code
		.replace(/'\.\/consentGap'/g, "'./consentGap.ts'")
		.replace(/'\.\/repos'/g, "'./repos.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props: { withheld } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('names the lane and states the share honestly, from the publishScope vocabulary', async () => {
	const html = await renderPopover({
		lane: 'corpus',
		unrecorded: ['Gurio/BeCenter'],
		unrecorded_ids: ['repo-1']
	});
	ok(html.includes('Corpus &amp; knowledge') || html.includes('Corpus & knowledge'));
	ok(html.includes('authored pages, kb, run bodies'));
	ok(html.includes('brnrd.dev'));
	ok(html.includes('leaves this machine'));
});

test('an unrecorded repo gets its own row and an enable action', async () => {
	const html = await renderPopover({
		lane: 'quota',
		unrecorded: ['Gurio/legacy'],
		unrecorded_ids: ['repo-1']
	});
	ok(html.includes('Gurio/legacy'));
	ok(html.includes('never recorded a publish scope'));
	ok(html.includes('enable here'));
});

test('an opted-out repo is named as having chosen to publish nothing, not as a gap', async () => {
	const html = await renderPopover({
		lane: 'activity',
		opted_out: ['Gurio/off'],
		opted_out_ids: ['repo-9']
	});
	ok(html.includes('Gurio/off'));
	ok(html.includes('chose to publish nothing'));
});

test('a name with no id twin renders no row for it', async () => {
	const html = await renderPopover({ lane: 'corpus', unrecorded: ['Gurio/legacy'] });
	ok(!html.includes('Gurio/legacy'));
	ok(html.includes('Nothing left to name here'));
});

test('the /repos link is always present as the aggregate fallback', async () => {
	const html = await renderPopover({
		lane: 'corpus',
		unrecorded: ['Gurio/BeCenter'],
		unrecorded_ids: ['repo-1']
	});
	equal((html.match(/href="\/repos"/g) ?? []).length >= 1, true);
});

test('an unknown lane token still renders without throwing, degraded but honest', async () => {
	const html = await renderPopover({
		lane: 'not-a-real-lane',
		unrecorded: ['Gurio/legacy'],
		unrecorded_ids: ['repo-1']
	});
	ok(html.includes('not-a-real-lane'));
	ok(html.includes("this lane's data"));
});
