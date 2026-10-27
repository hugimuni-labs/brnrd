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

// The fold is headline, install line, sign in, and the proof (threads/landing
// in brnrd-self, phase 2). The proof is brnrd's own board, not a scripted
// exchange: the hook is several tracks of work moving at once, each one
// followable. The 2026-09-06 hero's demo button left with the exchange.
test('the fold is the install line, sign in, and the board', async () => {
	const html = await renderLanding();
	ok(
		html.includes('curl -fsSL https://brnrd.dev/install | sh'),
		'the install line pipes the served installer'
	);
	ok(html.includes('>sign in</a'));
	ok(html.includes('src="/board.jpg"'), 'the proof is the board shot');
	ok(!html.includes('npm install -g brnrd'), 'no npm route the README does not document');
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
	ok(html.includes('id="install"'));
	ok(html.includes('same platforms, different topology'));
	ok(html.includes('href="/terms"'));
	ok(html.includes('href="/privacy"'));
});

// The loom (2026-10): a letter lands on a thread, a strand works it, a
// receipt comes back. The daemon-era nouns (paired daemon, routes bringing
// work, strands as bounded workers) described a product that no longer runs.
test('the landing speaks the loom, not the daemon', async () => {
	const html = (await renderLanding()).toLowerCase();
	for (const noun of ['letter', 'thread', 'strand', 'receipt']) ok(html.includes(noun), noun);
	for (const stale of ['daemon', 'paired', 'routes bring work', 'bounded workers'])
		ok(!html.includes(stale), `"${stale}" is gone`);
});
