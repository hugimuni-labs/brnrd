import { equal, ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import { runFace } from './runFace.ts';
import type { PickRow } from './pickLane.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'RunBlock.svelte');
const generated = join(here, '.runBlock.generated.mjs');

function pick(overrides: Partial<PickRow> & Pick<PickRow, 'id' | 'label'>): PickRow {
	return {
		kind: 'run',
		phase: 'picking',
		// `mood` joined PickRow required-with-null via THE FACE IN THREE
		// TENSES (merged alongside this file's own branch, 2026-08-03) —
		// the fixture defaults it like every other nullable field.
		mood: null,
		clock: null,
		note: null,
		color: '#f59e0b',
		urgency: 'calm',
		barFraction: 1,
		serves: [],
		crosses: [],
		...overrides
	};
}

// Same server-side render dance as ControlStrip.test.ts and WarpStack's tests:
// compile with `generate: 'server'`, drop the extension the compiler strips
// off relative specifiers so Node's ESM resolver can find them, and assert on
// the produced markup.
async function renderBlock(props: {
	burning: PickRow[];
	armed: PickRow[];
	open: boolean;
	docked?: boolean;
	error?: string | null;
	stale?: boolean;
	selectedId?: string | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'RunBlock' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props: { onToggle: () => {}, ...props } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

const alpha = pick({ id: 'run-a', label: 'Alpha', clock: '3m12s', note: null });
const bravo = pick({ id: 'run-b', label: 'Bravo', clock: '9m01s', note: null });

test('pulse: the head wears the lead — face, name, its own clock', async () => {
	const body = await renderBlock({ burning: [alpha], armed: [], open: false });
	ok(body.includes(runFace('run-a').glyph), "the lead's face renders");
	ok(body.includes('Alpha'), "the lead's name renders");
	ok(body.includes('3m12s'), "the lead's own clock renders");
});

// Hard constraint: pulse is pixel-identical to today whether or not a caller
// even knows about `selectedId` — omitting the prop and passing `null`
// explicitly must produce the exact same markup, byte for byte.
test('pulse is pixel-identical: no selection renders the same whether `selectedId` is omitted or null', async () => {
	const omitted = await renderBlock({ burning: [alpha, bravo], armed: [], open: false });
	const explicitNull = await renderBlock({
		burning: [alpha, bravo],
		armed: [],
		open: false,
		selectedId: null
	});
	equal(omitted, explicitNull);
});

test('inspection: selecting a different run swaps the face and name, not the tail', async () => {
	const pulse = await renderBlock({ burning: [alpha, bravo], armed: [], open: false });
	const inspecting = await renderBlock({
		burning: [alpha, bravo],
		armed: [],
		open: false,
		selectedId: 'run-b'
	});
	// Identity borrows the selection.
	ok(!pulse.includes('Bravo'), "pulse never names the run that isn't the lead");
	ok(inspecting.includes(runFace('run-b').glyph), "inspecting wears the selected run's face");
	ok(inspecting.includes('Bravo'), "inspecting wears the selected run's name");
	// The tail stays the lead's — the quiet proof the machine never stopped
	// being the machine underneath the borrowed face (the "+N / lead clock
	// stays in the tail" constraint).
	ok(inspecting.includes('3m12s'), "the lead's own clock stays in the tail");
	ok(
		!inspecting.includes('9m01s'),
		"the selected run's own clock is not the tail's — that would misattribute it to the lead"
	);
	ok(inspecting.includes('+1'), 'the further-strand count still says a second run burns');
});

test('selecting the lead itself is not a distinct mood — identical to pulse', async () => {
	const pulse = await renderBlock({ burning: [alpha, bravo], armed: [], open: false });
	const selectedLead = await renderBlock({
		burning: [alpha, bravo],
		armed: [],
		open: false,
		selectedId: 'run-a'
	});
	equal(pulse, selectedLead);
});

test('a selection that has since dropped out of `burning` falls back to the lead rather than naming nobody', async () => {
	const pulse = await renderBlock({ burning: [alpha], armed: [], open: false });
	const staleSelection = await renderBlock({
		burning: [alpha],
		armed: [],
		open: false,
		selectedId: 'run-long-gone'
	});
	equal(pulse, staleSelection);
});

test('a wake selection is not a run selection: the page never passes a wake id here, and nothing here would swap for one', async () => {
	// The page narrows `loomSelection` to `null` for a wake before this prop is
	// set (`+page.svelte`); this only pins that an arbitrary id the lead never
	// carried and `burning` never lists resolves the same graceful way a
	// stale run selection does — falling back to the lead, not to nobody.
	const pulse = await renderBlock({ burning: [alpha], armed: [], open: false });
	const wakeIdPassedByMistake = await renderBlock({
		burning: [alpha],
		armed: [],
		open: false,
		selectedId: 'wake-42'
	});
	equal(pulse, wakeIdPassedByMistake);
});
