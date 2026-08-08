import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'BoltSummons.svelte');
const generated = join(here, '.boltSummons.generated.mjs');

// The summons strip (design-the-bolt.md §The cloth side, fork 2 signed):
// steady state renders nothing, the count copy pluralizes correctly, and
// the two actions ("take all" / "view") are present once there's something
// to summon. Same compile-server-side dance as ControlStrip.test.ts.
async function renderStrip(props: {
	unacked: Array<Record<string, unknown>> | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'BoltSummons' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('steady state (0 unacked) renders literally nothing', async () => {
	// Svelte's server output always carries hydration comment markers around
	// an `{#if}` block, even when its branch is false — the honest "nothing"
	// this component owes is no *content*, not a byte-empty response.
	const empty = await renderStrip({ unacked: [] });
	ok(!empty.includes('await taking'), `expected no strip copy, got: ${empty}`);
	ok(!empty.includes('take all'), 'no take-all control at rest');
	ok(!empty.includes('role="status"'), 'no strip chrome at rest');

	const unresolved = await renderStrip({ unacked: null });
	ok(
		!unresolved.includes('await taking'),
		'count doctrine — unresolved feed also renders nothing, not a zero'
	);
});

test('one bolt renders the singular form', async () => {
	const body = await renderStrip({ unacked: [{ runId: 'run-1' }] });
	ok(body.includes('1 bolt awaits taking'), 'singular copy renders');
	ok(!body.includes('1 bolts'), 'never the plural noun at one');
});

test('several bolts render the plural form and both actions', async () => {
	const body = await renderStrip({
		unacked: [{ runId: 'run-1' }, { runId: 'run-2' }, { runId: 'run-3' }]
	});
	ok(body.includes('3 bolts await taking'), 'plural copy renders with the count');
	ok(body.includes('take all'), 'the take-all action renders');
	ok(body.includes('view'), 'the view action renders');
});
