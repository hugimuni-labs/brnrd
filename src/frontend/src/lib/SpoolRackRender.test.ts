import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { OFF_MARK } from './stateChrome.ts';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import type { RunnerProfile } from './runners.ts';

// w-68 rework (2026-08-19, the gauge/bench split): two of the maintainer's
// mid-flight steers landed in this component and both are pinned here —
// shell-then-core as a two-stage picker, and a row that shows an offerable/
// off binary, never a third "stale" or "?" state. `spoolRack.test.ts` covers
// the pure logic; this file is only the markup a reader actually sees, same
// SSR-compile dance `RailGaugeRender.test.ts` uses for this component's
// sibling.
const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'SpoolRack.svelte');
const generated = join(here, '.spoolRack.generated.mjs');

async function renderRack(props: {
	profiles: RunnerProfile[];
	shell?: string;
	defaultProfile?: string | null;
	stale?: boolean;
	wakeRequest?: null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'SpoolRack'
	});
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				// `shell` is required and is not a default the rack picks: the
				// caller knows it, because pressing that provider's fuel row is
				// what opened this rack. The harness stands in for that caller.
				shell: 'claude',
				defaultProfile: null,
				stale: false,
				wakeRequest: null,
				...props
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

// The two-stage picker (shell tabs, then that shell's cores) was the
// maintainer's own 2026-08-19 steer and it did its job — until the tab strip
// turned out to be a *second* place a provider could be selected, drifting
// from the page's own value and putting a codex core list under a claude
// heading. His follow-up on 2026-08-28 removed the stage rather than
// synchronising it: "the fuel bars would be clearly pressable, and they
// would contain the core/shell selection." The rack lists one shell's cores
// and offers no way to change which shell — that question is answered by
// which fuel row you pressed.
test("the rack lists one shell's cores and offers no way to change the shell", async () => {
	const body = await renderRack({
		profiles: [
			{ name: 'claude-haiku', shell: 'claude', available: true },
			{ name: 'claude-sonnet', shell: 'claude', available: true, selected: true },
			{ name: 'codex', shell: 'codex', available: true }
		]
	});
	ok(!body.includes('role="tablist"'), 'the shell selector is gone, not hidden');
	ok(!body.includes('role="tab"'), 'and so is every tab it held');
	// The named shell's cores render…
	ok(body.includes('claude-haiku'), "the named shell's cores render");
	ok(body.includes('claude-sonnet'), "the named shell's cores render");
	// …and no other shell's do, from the same catalog. Previously the codex
	// row was absent because a tab had not been picked; now it is absent
	// because this rack is not about codex at all.
	ok(!body.includes('>codex<'), "another shell's cores never leak in");
	// The header says which shell it is listing, since the tab strip that
	// used to answer that is gone.
	ok(body.includes('claude · cores') || body.includes('>claude<'), 'the rack names its shell');
});

test('the shell it lists is the one it was given, not one it resolved', async () => {
	// The old rack fell back to `defaultShell(...)` whenever nothing was
	// picked — a resolution step that only made sense while the rack owned a
	// selection. It owns none, so the pin no longer decides what is listed.
	const body = await renderRack({
		shell: 'codex',
		defaultProfile: 'claude-sonnet',
		profiles: [
			{ name: 'claude-sonnet', shell: 'claude', available: true, selected: true },
			{ name: 'codex-mini', shell: 'codex', available: true }
		]
	});
	ok(body.includes('codex-mini'), 'the requested shell is listed');
	ok(!body.includes('claude-sonnet'), 'the pinned profile does not drag its own shell in');
});

// His second steer, verbatim: unavailable stays, designed off, with its
// reason; "stale" never reaches the reader as its own state.
test('a verified-unavailable row renders off with its real reason, never the word "stale"', async () => {
	const body = await renderRack({
		shell: 'codex',
		profiles: [{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' }]
	});
	ok(body.includes('not installed on this daemon'), 'the concrete reason renders');
	ok(!/\bstale\b/iu.test(body), 'the word "stale" never renders on an off row');
	ok(body.includes('disabled'), 'the row is not a tappable control');
});

test('an unverified row ("we don\'t know") renders the same off bucket as verified-unavailable — no third state', async () => {
	const unavailable = await renderRack({
		shell: 'codex',
		profiles: [{ name: 'dead', shell: 'codex', available: false, availability: 'shell-not-found' }]
	});
	const unverified = await renderRack({
		shell: 'codex',
		profiles: [{ name: 'ghost', shell: 'codex' }]
	});
	ok(unverified.includes('disabled'), 'unverified never taps');
	// Same off recipe both ways — `border-dashed` is the *general* off
	// treatment now (design it off, don't grey it out), not a third state's
	// own distinguishing look.
	const offRecipe = /cursor-not-allowed border-dashed border-stone-700\/70 bg-stone-950\/40/u;
	ok(offRecipe.test(unavailable), 'verified-unavailable renders the shared off recipe');
	ok(
		offRecipe.test(unverified),
		'unverified renders the exact same off recipe, not a distinct one'
	);
	ok(
		!unverified.includes('? ghost'),
		'no "?" mark — that was the doubt the maintainer asked removed'
	);
});

test('a stale report disables every otherwise-available row, and the row itself never says "stale"', async () => {
	const body = await renderRack({
		profiles: [{ name: 'claude', shell: 'claude', available: true, selected: true }],
		stale: true
	});
	ok(
		body.includes('stale report'),
		'the account-wide chip still renders — a single global signal, not per-row'
	);
	ok(body.includes('disabled'), 'an available row is not tappable while the report is stale');
	// The row's own label/reason must not use the word the account-wide chip
	// is allowed to use — that word on a *row* is exactly the doubt this
	// steer removed.
	const rowSection = body.slice(body.indexOf('space-y-1.5'));
	ok(!/\bstale\b/iu.test(rowSection), 'the row itself never renders the word "stale"');
});

test('a row whose own daemon report is stale is disabled even when the account-wide report is not', async () => {
	const body = await renderRack({
		profiles: [{ name: 'claude', shell: 'claude', available: true, daemon_stale: true }],
		stale: false
	});
	ok(
		!body.includes('stale report'),
		'the account-wide chip stays quiet — only this row is the problem'
	);
	ok(body.includes('disabled'), 'the row itself does not tap');
});

test('a shell with only dead cores still lists them, designed off — never hidden', async () => {
	// This pinned "the dead shell still offers a tab" while the tab strip
	// existed. The fact it was protecting survives the strip's removal: an
	// entirely-unavailable provider is still pressable from its fuel row and
	// still renders, with the reason. Off is legitimate and stays.
	const body = await renderRack({
		shell: 'claude',
		profiles: [
			{
				name: 'claude-bare-api-only',
				shell: 'claude',
				available: false,
				availability: 'auth-env-missing'
			}
		]
	});
	ok(body.includes('claude-bare-api-only'), 'the dead row renders rather than vanishing');
	ok(body.includes('auth not configured'), 'with its concrete reason');
	ok(body.includes(OFF_MARK), 'and the off grammar, not a dim variant of the live one');
});

test('a pinned row whose core is unpinned never prints "default" for two different meanings', async () => {
	const body = await renderRack({
		profiles: [{ name: 'claude', shell: 'claude', available: true, selected: true }]
	});
	const defaultOccurrences = (body.match(/>default</g) ?? []).length;
	equal(
		defaultOccurrences,
		1,
		'only the pin badge renders the word "default" — the core label must use different wording'
	);
});

// w-68's own bar: a row shows what you need to choose, not what justified it.
test("rank, quota source, and capability move to the row's own open state, off the row line", async () => {
	const body = await renderRack({
		profiles: [
			{
				name: 'claude-sonnet',
				shell: 'claude',
				available: true,
				selected: true,
				cost_rank: 30,
				quota_source: 'claude-local',
				capability_score: 82
			}
		]
	});
	ok(!body.includes('rank 30'), 'rank does not render on the row line by default');
	ok(!body.includes('cap 82'), 'capability does not render on the row line by default');
	ok(body.includes('detail'), 'the row offers a disclosure for the justification fields');
});

test('the rack keeps no provider cursor of its own — there is no control to keep one for', () => {
	// First it kept a `manualShell` `$state` seeded from a prop and free to
	// drift. #1671 made it render the page's value instead. This deletes the
	// control entirely: `shell` is a required input, so there is neither a
	// stored selection nor a way to change one from in here.
	const source = readFileSync(componentPath, 'utf8');
	ok(!/manualShell/u.test(source), 'no local copy of the selection');
	ok(!/onShellSelect/u.test(source), 'and no callback to change it from here');
	ok(
		!/role="tablist"/u.test(source),
		'the tab strip is gone from the source, not merely unrendered'
	);
	ok(!/defaultShell/u.test(source), 'and the fallback resolution that only a cursor needed');
	ok(/shell: string;/u.test(source), 'the shell is a required input');
});

test('a core-scope allowance renders on the row where that core is picked', () => {
	// The number that decides a `claude-fable` tap is fable's own weekly
	// allowance. On the claude fuel bar it was a third overlaid fill; here
	// it sits on the row it constrains, matched on the model the profile
	// pins rather than on the profile's name.
	const source = readFileSync(componentPath, 'utf8');
	ok(/coreAllowances\.get\(model\)/u.test(source), 'matched by pinned model, not by name');
	ok(/allowance\.percent\)\}% allowance`/u.test(source), 'and rendered on the row');
});
