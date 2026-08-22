import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { DOCS_URL } from './publicStats.ts';
import type { MessengerDoor } from './repos.ts';

// brr/every-door-on-the-page — same compile-and-render-for-real dance
// `ColdStart.test.ts` uses: a claim about the rendered HTML only fails
// here if it's actually false in the markup, not in a hand-written stub.
//
// Named `MessengerDoorsPanel.test.ts`, not `MessengerDoors.test.ts` —
// deliberately, not a typo: this repo's checkout lives on a
// case-insensitive filesystem (macOS/APFS default), and
// `messengerDoors.test.ts` (the pure-logic twin for `messengerDoors.ts`)
// already claims that name up to case. Two files differing only in case
// collide to one inode there — a real trap hit once while authoring this
// very file — so the component test earns a distinct word instead.
const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'MessengerDoors.svelte');
const generated = join(here, '.messengerDoorsPanel.generated.mjs');

async function renderDoors(
	doors: MessengerDoor[] | null,
	nowOverride: number | null = 0
): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'MessengerDoors' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { doors, nowOverride } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function door(over: Partial<MessengerDoor>): MessengerDoor {
	return { platform: 'x', deep_link_available: false, reason: null, ...over };
}

const FULL_REGISTRY: MessengerDoor[] = [
	door({ platform: 'telegram', deep_link_available: true, reason: null }),
	door({ platform: 'whatsapp', deep_link_available: false, reason: 'not_configured' }),
	door({ platform: 'slack', deep_link_available: false, reason: 'not_built' }),
	door({ platform: 'signal', deep_link_available: false, reason: 'not_built' })
];

// Svelte 5's SSR output for a false `{#if}` is a pair of hydration comment
// anchors, not the empty string — assert on the absence of real content
// instead of a byte-exact empty body.
test('null doors (fetch not landed yet) renders nothing', async () => {
	const html = await renderDoors(null);
	ok(!html.includes('connect a chat'));
	ok(!html.includes('data-testid="door-'));
});

test('empty doors renders nothing rather than an empty panel shell', async () => {
	const html = await renderDoors([]);
	ok(!html.includes('connect a chat'));
	ok(!html.includes('data-testid="door-'));
});

test('implemented doors render while roadmap-only connectors stay out of account controls', async () => {
	const html = await renderDoors(FULL_REGISTRY);
	ok(html.includes('data-testid="door-telegram"'));
	ok(html.includes('data-testid="door-whatsapp"'));
	ok(!html.includes('data-testid="door-slack"'));
	ok(!html.includes('data-testid="door-signal"'));
});

test('a lit door offers a connect button, not an off badge', async () => {
	const html = await renderDoors(FULL_REGISTRY);
	ok(html.includes('data-testid="connect-telegram"'));
	ok(html.includes('connect telegram'));
});

test('a paired door renders the existing identity before offering another connection', async () => {
	const html = await renderDoors([
		door({
			platform: 'whatsapp',
			deep_link_available: true,
			paired: true,
			paired_count: 1,
			paired_display: 'Alexandra'
		})
	]);
	ok(html.includes('connected'));
	ok(html.includes('Alexandra'));
	ok(html.includes('connect another chat'));
	ok(!html.includes('>connect whatsapp<'), 'the stale primary action is gone');
});

test('a dark door with no lever (not_built) is not presented as a broken control', async () => {
	const html = await renderDoors(FULL_REGISTRY);
	ok(!html.includes('data-testid="door-slack"'));
	ok(!html.includes('no connector'));
});

test('a dark door with a lever (not_configured) shows the off state and a docs link', async () => {
	const html = await renderDoors(FULL_REGISTRY);
	const whatsappRow = html.slice(
		html.indexOf('data-testid="door-whatsapp"'),
		html.indexOf('data-testid="door-slack"')
	);
	ok(whatsappRow.includes('off</span'), 'the off badge renders');
	ok(whatsappRow.includes('not configured'), 'the not_configured copy renders');
	ok(whatsappRow.includes(DOCS_URL), 'a docs link is offered — there is a real lever to pull');
});

test('an unknown reason still renders the row with the generic off copy, never a blank one', async () => {
	const html = await renderDoors([
		door({ platform: 'irc', deep_link_available: false, reason: 'a-future-backend-reason' })
	]);
	ok(html.includes('data-testid="door-irc"'));
	ok(html.includes('not available on this deployment'));
});

test('configured-but-unavailable and lit doors carry visibly different marker shapes', async () => {
	const html = await renderDoors(FULL_REGISTRY);
	const litRow = html.slice(
		html.indexOf('data-testid="door-telegram"'),
		html.indexOf('data-testid="door-whatsapp"')
	);
	const darkRow = html.slice(html.indexOf('data-testid="door-whatsapp"'));
	ok(litRow.includes('rounded-full'), 'a lit door gets a round marker');
	ok(!darkRow.includes('rounded-full'), 'a dark door does not reuse the round marker, dimmed');
});
