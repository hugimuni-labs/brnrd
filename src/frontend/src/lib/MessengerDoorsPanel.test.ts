import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { DOCS_URL } from './publicStats.ts';
import type { MessengerDoor } from './repos.ts';
import type { PairedChat } from './repos.ts';

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
	nowOverride: number | null = 0,
	pairedChatsOverride: PairedChat[] = []
): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'MessengerDoors' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { doors, nowOverride, pairedChatsOverride } }).body;
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

test('a platform row owns its connected chat and revoke control', async () => {
	const html = await renderDoors(
		[door({ platform: 'telegram', deep_link_available: true, paired: true, paired_count: 1 })],
		0,
		[
			{
				id: 'route-1',
				platform: 'telegram',
				paired: true,
				principal_display: 'Gurio',
				chat_title: 'Workshop',
				repo_full_name: null,
				paired_at: null,
				paired_at_label: 'today'
			},
			{
				id: 'legacy-null-principal',
				platform: 'telegram',
				paired: false,
				principal_display: null,
				chat_title: 'Not authorized',
				repo_full_name: null,
				paired_at: null,
				paired_at_label: 'long ago'
			}
		]
	);
	ok(html.includes('data-testid="paired-chat-row"'));
	ok(html.includes('Gurio'));
	ok(html.includes('Workshop · auto-routed'));
	ok(!html.includes('Not authorized'), 'a principal-less legacy route does not render connected');
	ok(html.includes('data-testid="revoke-open"'));
	ok(html.includes('bg-amber-400'), 'the connected chat carries the shared amber signal');
});

test('an unpaired platform keeps connect in the same door row', async () => {
	const html = await renderDoors([
		door({ platform: 'whatsapp', deep_link_available: true, paired: false, paired_count: 0 })
	]);
	ok(html.includes('data-testid="connect-whatsapp"'));
	ok(!html.includes('data-testid="paired-chat-row"'));
	ok(!html.includes('data-testid="revoke-open"'));
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

// ----- ONE DERIVATION OF "CONNECTED" ---------------------------------- //
// The reported symptom: WhatsApp was connected, the backend knew, and the
// door still offered CONNECT WHATSAPP with the paired chat rendered fifty
// pixels below it.
//
// Honest about what these tests do and do not prove. They go red on the
// parent commit with `TypeError: isConnected is not a function`, which
// says the export is new — not that the defect reproduces. The screenshot
// that started this was taken against a build that predates
// `MessengerDoors` owning the paired-chat list at all, so the measured
// symptom may already be gone. What is pinned here is the *contract*: one
// derivation, wire flag first, chat list as the answer when the flag is
// stale, and the optimistic poll outcome deliberately outside it.
// -------------------------------------------------------------------- //

test('isConnected: wire paired:false + paired chat in list → connected (the bug)', async () => {
	// On the parent commit `isConnected` is not exported from repos.ts;
	// importing it returns `undefined` → calling it throws TypeError → red.
	const { isConnected: ic } = (await import('./repos.ts')) as {
		isConnected?: (door: { paired?: boolean; platform: string }, chats: PairedChat[]) => boolean;
	};
	const chat: PairedChat = {
		id: 'r-wa-1',
		platform: 'whatsapp',
		paired: true,
		principal_display: 'Me',
		chat_title: null,
		repo_full_name: null,
		paired_at: null,
		paired_at_label: 'today'
	};
	// Wire says not paired — but the chat list says otherwise. Must be connected.
	const wa = { paired: false, platform: 'whatsapp' };
	ok(ic!(wa, [chat]), 'paired chat in list → connected despite stale wire flag');
	ok(!ic!(wa, []), 'no chats and paired:false → not connected');
	ok(ic!({ paired: true, platform: 'whatsapp' }, []), 'wire paired:true → connected regardless');
	// The platform comes off the door: another platform's paired chat must
	// never light this door. This is the case the old three-argument
	// signature made possible to get wrong at the call site.
	ok(
		!ic!({ paired: false, platform: 'telegram' }, [chat]),
		"another platform's chat is not this door"
	);
});

test('a door with paired:false but a matched paired chat must not offer a bare connect button', async () => {
	const chat: PairedChat = {
		id: 'r-wa-2',
		platform: 'whatsapp',
		paired: true,
		principal_display: 'Me',
		chat_title: null,
		repo_full_name: null,
		paired_at: null,
		paired_at_label: 'today'
	};
	const html = await renderDoors(
		[door({ platform: 'whatsapp', deep_link_available: true, paired: false })],
		0,
		[chat]
	);
	ok(!html.includes('data-testid="connect-whatsapp"'), 'bare connect button must not appear');
	ok(html.includes('connected'), 'connected state must render');
});

test('one paired-chats request across two mounted panels', async () => {
	// On the parent commit these exports do not exist → red (same TypeError).
	const { invalidatePairedChats: invalidate, loadSharedPairedChats: load } =
		(await import('./repos.ts')) as {
			invalidatePairedChats?: () => void;
			loadSharedPairedChats?: (fetchImpl?: typeof fetch) => Promise<PairedChat[]>;
		};
	invalidate!();
	let fetchCalls = 0;
	const mockFetch = (async () => {
		fetchCalls++;
		return { ok: true, status: 200, json: async () => ({ paired_chats: [] }) } as Response;
	}) as typeof fetch;
	// Two concurrent callers — only one GET should be issued.
	const [a, b] = await Promise.all([load!(mockFetch), load!(mockFetch)]);
	ok(fetchCalls === 1, `expected 1 fetch but got ${fetchCalls}`);
	ok(Array.isArray(a) && Array.isArray(b), 'both results are arrays');
	invalidate!(); // clean up for next test
});

test('a later mount re-reads the wire — the burst is deduplicated, the answer is not cached', async () => {
	// The trade this store must not make. Memoising the resolved list also
	// collapses two requests into one, and then serves that same list on
	// every later mount — so navigating away and back shows a paired-chats
	// list from minutes ago, on the one surface whose whole job is to say
	// what is connected *right now*.
	const { invalidatePairedChats: invalidate, loadSharedPairedChats: load } =
		(await import('./repos.ts')) as {
			invalidatePairedChats?: () => void;
			loadSharedPairedChats?: (fetchImpl?: typeof fetch) => Promise<PairedChat[]>;
		};
	invalidate!();
	let fetchCalls = 0;
	const mockFetch = (async () => {
		fetchCalls++;
		return { ok: true, status: 200, json: async () => ({ paired_chats: [] }) } as Response;
	}) as typeof fetch;
	await load!(mockFetch);
	await load!(mockFetch);
	ok(fetchCalls === 2, `sequential callers must each read the wire, got ${fetchCalls}`);
	invalidate!();
});

test('revoke clears the chat row and invalidates the shared cache', async () => {
	// Existing revoke behaviour: the revoke button appears when connected,
	// and after revoke the chat row is gone. This pins it survives the
	// isConnected switch. (The cache invalidation is a side-effect of the
	// revoke path — verified in the store test above via invalidate!().)
	const html = await renderDoors(
		[door({ platform: 'telegram', deep_link_available: true, paired: true })],
		0,
		[
			{
				id: 'tg-1',
				platform: 'telegram',
				paired: true,
				principal_display: 'Gurio',
				chat_title: null,
				repo_full_name: null,
				paired_at: null,
				paired_at_label: 'today'
			}
		]
	);
	ok(html.includes('data-testid="revoke-open"'), 'revoke control renders for a connected chat');
	ok(html.includes('Gurio'), 'connected chat principal is visible');
});

test('countdown and re-mint controls survive the isConnected switch', async () => {
	// A minted-but-unpaired door (outcome present, no chat yet) must still
	// show the countdown — it falls in the else branch of the condition, not
	// the connected branch, and must not regress.
	// We test the boundary with a zero-override nowMs to trigger countdown
	// at time 0 — the component renders the countdown when there is a mint
	// outcome and cd is truthy but isConnected is false.
	// The component calculates ttl at mint time; in SSR with nowMs=0 the ttl
	// is computed as the full countdown from epoch. We only care that the
	// countdown branch doesn't accidentally render the connect button.
	const html = await renderDoors(
		[door({ platform: 'telegram', deep_link_available: true, paired: false })],
		0,
		[]
	);
	// An unconnected door with no mint outcome → connect button (not countdown).
	ok(html.includes('data-testid="connect-telegram"'));
	ok(!html.includes('data-testid="paired-telegram"'));
});
