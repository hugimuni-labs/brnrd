import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

// Same rendering dance as termsPrivacyLink.test.ts: compile the real
// component and render its real markup, so a claim that only becomes false
// in the rendered HTML (a link silently pointing at the wrong path, copy
// dropped in a refactor) still fails here. `$app/paths` is swapped for the
// identity function it is at this app's root base path; the three child
// components below are stubbed with no-ops because this file asserts
// Landing's own copy and links, not their internals (uncovered elsewhere —
// none of the three has its own test yet).
const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'Landing.svelte');
const generated = join(here, '.landing.generated.mjs');

async function renderLanding(): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'Landing' });
	const runnable = compiled.js.code
		.replace(
			/import WinkWordmark from '\$lib\/WinkWordmark\.svelte';/,
			'const WinkWordmark = () => {};'
		)
		.replace(
			/import HeroExchange from '\$lib\/HeroExchange\.svelte';/,
			'const HeroExchange = () => {};'
		)
		.replace(/import ShelfIcon from '\$lib\/ShelfIcon\.svelte';/, 'const ShelfIcon = () => {};')
		.replace(/'\$lib\/publicStats'/g, "'./publicStats.ts'")
		.replace(/'\$lib\/legalNotice'/g, "'./legalNotice.ts'")
		.replace(/'\$lib\/transitions'/g, "'./transitions.ts'")
		.replace(/'\$lib\/supportMatrix'/g, "'./supportMatrix.ts'")
		.replace(/'\$lib\/login'/g, "'./login.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

// The 2026-09-06 redesign's whole point (maintainer ask: a "real
// outcome-first hero", not identity-then-topology). Pin the headline so a
// future edit can't quietly drift back to leading on what brnrd *is* rather
// than what a visitor gets back.
test('the hero leads with an outcome, not identity', async () => {
	const html = await renderLanding();
	ok(html.includes('assign the work. get back a receipt.'));
});

// "Add visible 'Explore the live demo' leading to existing /new (inspect it;
// label demo honestly)": the target is `/new?demo` specifically, not bare
// `/new` — bare `/new` shows "sign in to see the room" to an anonymous
// visitor (see routes/new/+page.svelte), which would make the landing's own
// CTA a dead end for exactly the reader it is aimed at. The disclosure line
// keeps the label from over-promising a live feed of the visitor's own runs.
test('the demo CTA targets the honest public route and discloses what it shows', async () => {
	const html = await renderLanding();
	ok(html.includes('href="/new?demo"'), 'links to the demo-flagged route, not bare /new');
	ok(html.includes('explore the demo'));
	ok(
		html.includes('scripted demo with sample data'),
		'discloses that the anonymous demo is a replay, not a live feed of the visitor'
	);
});

// The command-deck strip: three real beats (assign / work / receipt), not an
// invented progress meter. Pins that all three render together so a future
// edit can't drop one silently.
test('the how-work-moves strip renders all three real beats', async () => {
	const html = await renderLanding();
	for (const verb of ['assign', 'work', 'receipt']) {
		ok(html.includes(`>${verb}</span>`), `"${verb}" step renders`);
	}
});

// The install, reach, and legal surfaces are unchanged by the hero
// redesign — this pins that the redesign didn't quietly drop any of them.
test('install, reach, and legal surfaces still render', async () => {
	const html = await renderLanding();
	ok(html.includes('npm install -g brnrd'));
	ok(html.includes('same platforms, different topology'));
	ok(html.includes('href="/terms"'));
	ok(html.includes('href="/privacy"'));
});
