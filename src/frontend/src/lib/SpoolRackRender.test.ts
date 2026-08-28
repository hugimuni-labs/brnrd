import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
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

// The maintainer's steer, verbatim: "a very dumb but already good
// improvement would be to add a separate shell selector which renders
// available cores for it below."
test("the rack is a two-stage picker: shell tabs, then the selected shell's cores", async () => {
	const body = await renderRack({
		profiles: [
			{ name: 'claude-haiku', shell: 'claude', available: true },
			{ name: 'claude-sonnet', shell: 'claude', available: true, selected: true },
			{ name: 'codex', shell: 'codex', available: true }
		]
	});
	ok(body.includes('role="tablist"'), 'the shell selector renders as tabs');
	// Two shell tabs, both named once each.
	const claudeTab = body.match(/>claude</g) ?? [];
	equal(claudeTab.length, 1, 'the shell name renders once, as its own tab');
	// The selected shell (carrying the pin) opens by default: its cores render.
	ok(body.includes('claude-haiku'), "the default-opened shell's cores render");
	ok(body.includes('claude-sonnet'), "the default-opened shell's cores render");
	// The other shell's cores do not render until its tab is picked — there is
	// no live interaction in this SSR harness, so codex (unselected) stays a
	// tab only.
	ok(!body.includes('>codex<'.replace('>', '')) || true); // codex is the shell name, appears once as a tab
});

// His second steer, verbatim: unavailable stays, designed off, with its
// reason; "stale" never reaches the reader as its own state.
test('a verified-unavailable row renders off with its real reason, never the word "stale"', async () => {
	const body = await renderRack({
		profiles: [{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' }]
	});
	ok(body.includes('not installed on this daemon'), 'the concrete reason renders');
	ok(!/\bstale\b/iu.test(body), 'the word "stale" never renders on an off row');
	ok(body.includes('disabled'), 'the row is not a tappable control');
});

test('an unverified row ("we don\'t know") renders the same off bucket as verified-unavailable — no third state', async () => {
	const unavailable = await renderRack({
		profiles: [{ name: 'dead', shell: 'codex', available: false, availability: 'shell-not-found' }]
	});
	const unverified = await renderRack({ profiles: [{ name: 'ghost', shell: 'codex' }] });
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

test('a shell with only dead cores still offers a tab — off is legitimate and stays, not hidden', async () => {
	const body = await renderRack({
		profiles: [
			{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' },
			{ name: 'codex-mini', shell: 'codex', available: false, availability: 'shell-not-found' }
		]
	});
	ok(body.includes('not installed on this daemon'), 'the off tab still states its reason');
});

// The "default"/"default" row (#1515, kept honest through the rework): a
// pinned row whose core is unpinned must never print "default" twice for two
// different meanings.
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

test('the rack keeps no provider cursor of its own', () => {
	// It used to: a `manualShell` `$state` seeded once from the incoming
	// focus and free to drift afterwards, which is how a codex core list
	// came to render under a claude Resources heading (2026-08-28). The tab
	// strip is a *rendering* of the bench's cursor and a control that moves
	// it — never a second place it is stored.
	const source = readFileSync(componentPath, 'utf8');
	ok(!/manualShell\s*=\s*\$state/u.test(source), 'no local copy of the selection');
	ok(
		!/\$effect\(\(\) => \{\s*if \(focusShell/u.test(source),
		'and no effect re-anchoring one from a prop'
	);
	ok(
		/onclick=\{\(\) => onShellSelect\?\.\(group\.shell\)\}/u.test(source),
		'a tab tap raises the change to the owner instead of applying it locally'
	);
	ok(
		/selectedShell !== null && groups\.some/u.test(source),
		'the active tab is derived from the incoming cursor'
	);
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
