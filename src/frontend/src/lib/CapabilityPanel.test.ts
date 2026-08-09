import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { Capability, ConnectedRepo } from './repos.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'CapabilityPanel.svelte');
const generated = join(here, '.capabilityPanel.generated.mjs');

// Same rendering dance as ColdStart.test.ts / RunBlock.test.ts: compile the
// real component with `generate: 'server'` and mount it through
// `svelte/server`, so this exercises the component's real render path (the
// same `groupRows`/`machineIsGhost` logic the browser runs), not a
// hand-rolled reimplementation of the collapse rule.
async function renderPanel(props: {
	capabilities: Capability[] | null;
	connectedRepos?: ConnectedRepo[] | null;
	pairingCommand?: string | null;
	now?: number;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'CapabilityPanel' });
	const runnable = compiled.js.code
		.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				connectedRepos: null,
				pairingCommand: null,
				now: Date.parse('2026-08-09T21:00:00Z'),
				...props
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function cap(over: Partial<Capability> = {}): Capability {
	return {
		id: 'cli-installed',
		scope: 'machine',
		subject: 'deadbeef1234',
		state: 'lit',
		evidence: { source: 'daemon-heartbeat', as_of: null },
		requires: [],
		heat: 'required',
		act: { kind: 'none', target: null },
		frontier: false,
		...over
	};
}

// A machine's `daemon-live` row, dark past #1268's staleness horizon
// (`capabilities.py::_DAEMON_ONLINE_AFTER`, 2 minutes) — this is the exact
// wire shape a real ghost machine sends: `daemon-live` dark with a stale
// `evidence.as_of`, everything downstream of it either dark or
// `unobservable` because nothing can measure a dead machine.
function ghostMachineCaps(subject: string): Capability[] {
	return [
		cap({
			id: 'daemon-live',
			subject,
			state: 'dark',
			evidence: { source: 'daemon-heartbeat', as_of: '2026-07-01T00:00:00Z' }
		}),
		cap({ id: 'cli-installed', subject, state: 'lit' }),
		cap({ id: 'machine-paired', subject, state: 'dark' }),
		cap({
			id: 'runner-available',
			subject,
			state: 'unobservable',
			evidence: { source: 'none', as_of: null }
		})
	];
}

// #1275: the defect, reproduced. Before the fix, the collapse rule only
// ever asked "is every row lit" — a ghost machine's `daemon-live` row is
// dark by construction, so `allLit` was always false and the group rendered
// every capability row plus an "unobservable" count, expanded forever.
test('a ghost machine folds to one line instead of expanding forever', async () => {
	const html = await renderPanel({ capabilities: ghostMachineCaps('deadbeef1234') });
	ok(html.includes('not live'), 'the fold line names the machine as not live');
	ok(html.includes('4 capabilities hidden'), 'the fold line counts the hidden rows');
	ok(!html.includes('CLI installed'), 'individual capability rows do not render while folded');
	ok(!html.includes('machine paired'), 'individual capability rows do not render while folded');
	ok(
		!html.includes('unobservable from here'),
		'the per-group unobservable count is folded away too, not shown alongside the summary'
	);
});

// The other half of the same regression, guarded so a fix that folds too
// eagerly (e.g. "collapse anything not fully lit") cannot pass silently:
// a live machine with a genuinely unlit, actionable row must stay expanded
// — that row is exactly what the reader came to the board to see.
test('a live machine with a dark, actionable row stays expanded — only ghosts fold', async () => {
	const subject = 'a4095fdead99';
	const html = await renderPanel({
		capabilities: [
			cap({ id: 'daemon-live', subject, state: 'lit' }),
			cap({ id: 'cli-installed', subject, state: 'lit' }),
			cap({ id: 'runner-available', subject, state: 'dark' })
		]
	});
	ok(!html.includes('not live'), 'a live machine never renders the ghost fold line');
	ok(!html.includes('capabilities hidden'), 'a live machine never renders the ghost fold line');
	ok(html.includes('a runner is available'), 'the dark, actionable row renders in full');
});

// Regression guard for #1268's original behaviour: a fully-lit *live*
// machine still collapses to the quiet "all N lit" summary — #1275 adds a
// second fold reason, it does not touch the first.
test('a fully-lit live machine still collapses to "all N lit"', async () => {
	const subject = 'a4095fdead99';
	const html = await renderPanel({
		capabilities: [
			cap({ id: 'daemon-live', subject, state: 'lit' }),
			cap({ id: 'cli-installed', subject, state: 'lit' }),
			cap({ id: 'machine-paired', subject, state: 'lit' })
		]
	});
	ok(html.includes('all 3 lit'), 'the pre-existing lit-collapse summary still renders');
	ok(!html.includes('not live'), 'a live machine never renders the ghost fold line');
});

// A machine group with no `daemon-live` row at all (shouldn't happen for a
// real daemon, but a catalog row can't assume its neighbour is present) must
// not be treated as a ghost — no signal to fold on beats a false fold that
// hides rows the reader has no other way to see.
test('a machine group with no daemon-live row is never folded as a ghost', async () => {
	const subject = 'no-live-row01';
	const html = await renderPanel({
		capabilities: [
			cap({ id: 'cli-installed', subject, state: 'lit' }),
			cap({ id: 'machine-paired', subject, state: 'dark' })
		]
	});
	ok(!html.includes('not live'), 'no daemon-live row means no ghost verdict to render');
	ok(html.includes('machine paired'), 'rows render in full absent a lit-or-ghost verdict');
});
