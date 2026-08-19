import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import type { RunnerProfile } from './runners.ts';

// 2026-08-19 rework ("the rack of dead spools"): the component's tap-gating
// and grouping logic moved to `spoolRack.ts` (see `spoolRack.test.ts`), but
// the markup itself — which attribute actually carries `disabled`, whether
// a collapsed shell really renders one line instead of N — is only visible
// by rendering the component, same SSR-compile dance `ControlStripRender.
// test.ts` already uses for this component's sibling.
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

test('a row missing `available` renders unverified — dashed, disabled, marked "?" — never as a live control', async () => {
	const body = await renderRack({ profiles: [{ name: 'ghost', shell: 'codex' }] });
	ok(body.includes('? ghost'), 'the unverified mark renders on the name');
	ok(body.includes('border-dashed'), 'unverified gets its own visual treatment, not the ✗ one');
	ok(body.includes('disabled'), 'the row is not a tappable control');
});

test('a stale report disables every otherwise-available row', async () => {
	const body = await renderRack({
		profiles: [{ name: 'claude', shell: 'claude', available: true, selected: true }],
		stale: true
	});
	ok(body.includes('stale report'), 'the account-wide chip still renders');
	ok(body.includes('disabled'), 'an available row is not tappable while the report is stale');
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

test('a shell with only dead cores collapses to one summary line, not one row per core', async () => {
	const body = await renderRack({
		profiles: [
			{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' },
			{ name: 'codex-mini', shell: 'codex', available: false, availability: 'shell-not-found' },
			{ name: 'codex-full', shell: 'codex', available: false, availability: 'shell-not-found' }
		]
	});
	ok(
		body.includes('codex — not installed on this daemon · 3 cores'),
		'one collapsed line names the shell and count'
	);
	ok(!body.includes('codex-mini'), 'individual dead core rows do not render while collapsed');
	ok(!body.includes('codex-full'), 'individual dead core rows do not render while collapsed');
});

test('an available shell renders its cores as individual rows, grouped under one shell header', async () => {
	const body = await renderRack({
		profiles: [
			{ name: 'claude-haiku', shell: 'claude', available: true, cost_rank: 10 },
			{ name: 'claude-sonnet', shell: 'claude', available: true, cost_rank: 30, selected: true }
		]
	});
	ok(body.includes('claude-haiku'), 'first core row renders');
	ok(body.includes('claude-sonnet'), 'second core row renders');
	const shellHeaders = body.match(/>claude</g) ?? [];
	equal(shellHeaders.length, 1, 'the shell name renders once, as the group header, not per row');
});
