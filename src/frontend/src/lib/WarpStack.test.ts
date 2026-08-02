import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { WarpLayer } from './warp.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'WarpStack.svelte');
const generated = join(here, '.warpStack.generated.mjs');

// The warp stack's visible contract (design-work-layers.md §Interaction,
// restructured 2026-08-02 — the stack is the standing body and stands open):
// every layer shows its ember items inline with no click; the held remainder
// (definition, banked/cold) lives behind the layer's fold with the counts on
// the band. An ember's mandate is a further, three-level disclosure of its
// own (2026-08-02, "the mandate folds": the ignite·copy block dominated the
// phone viewport): a collapsed layer shows the item's headline and heat only;
// an open layer with the item unselected shows a reduced, clamped mandate
// line with ignition still reachable; selecting the item shows the full
// bordered mandate block, as before. Same server-side render dance as
// BackchannelQueue's tests: compile with stubbed children, assert on the
// produced markup.
async function renderStack(props: {
	layers: WarpLayer[];
	initialOpenCallSign?: string | null;
	initialOpenItemKey?: string | null;
	onOpenPage?: (path: string) => void;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'WarpStack'
	});
	const runnable = compiled.js.code
		.replace(
			/import\s+MarkdownContent\s+from\s*'\.\/MarkdownContent\.svelte';/,
			'const MarkdownContent = () => {};'
		)
		.replace(/'\.\/statusPalette'/g, "'./statusPalette.ts'")
		.replace(/'\.\/warp'/g, "'./warp.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function layer(overrides: Partial<WarpLayer>): WarpLayer {
	return {
		callSign: 'the-loom',
		path: 'surface/layers/the-loom.md',
		definitionMarkdown: 'The redesign band.',
		items: [],
		counts: { ember: 0, banked: 0, cold: 0, unstated: 0 },
		...overrides
	};
}

const EMBER_ITEM = {
	key: '0:restructure',
	headline: 'The restructure',
	kind: null,
	state: 'ember' as const,
	needs: null,
	refs: [],
	prompt: 'Implement the restructure.',
	taken: [],
	bodyMarkdown: 'Body.'
};

const BANKED_ITEM = {
	key: '1:past-band',
	headline: 'The past band',
	kind: null,
	state: 'banked' as const,
	needs: 'the restructure landing first',
	refs: [],
	prompt: 'Build the cloth window.',
	taken: ['run-260802-0001-9qgz'],
	bodyMarkdown: 'Body.'
};

test('a folded band shows call sign, heat counts, and ember headlines — no mandate text at all', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [EMBER_ITEM, BANKED_ITEM],
				counts: { ember: 1, banked: 1, cold: 0, unstated: 0 }
			})
		]
	});
	ok(body.includes('the-loom'));
	ok(body.includes('1 ember'));
	ok(body.includes('1 banked'));
	// The embers stand open: the section header's "N ember" is visible work.
	ok(body.includes('The restructure'), 'ember item headline renders inline on the folded band');
	// Deviation from the pre-2026-08-02 contract: the mandate used to ride
	// the standing row unconditionally, which is exactly what dominated the
	// phone viewport (maintainer feedback). The three-level disclosure now
	// hides the mandate — and its ignite·copy affordance — entirely while
	// the layer is collapsed; state 1 is headline + heat only.
	ok(
		!body.includes('Implement the restructure.'),
		'the ember mandate text stays folded while the layer is collapsed'
	);
	ok(
		!body.includes('ignite · copy'),
		'the ignition affordance folds with the mandate on a collapsed layer'
	);
	// The held remainder stays behind the fold.
	ok(!body.includes('The past band'), 'banked item leaks out of the folded band');
	ok(!body.includes('Build the cloth window.'), 'banked prompt leaks out of the folded band');
});

test('an open layer with the item unselected shows a reduced, clamped mandate — ignition stays on the row', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [EMBER_ITEM, BANKED_ITEM],
				counts: { ember: 1, banked: 1, cold: 0, unstated: 0 }
			})
		],
		initialOpenCallSign: 'the-loom'
	});
	ok(body.includes('The restructure'), 'the headline still renders');
	// State 2: the mandate text is present but visually reduced — clamped,
	// no bordered block — and the ignite affordance rides the same row.
	ok(
		body.includes('Implement the restructure.'),
		'the reduced mandate carries the full text (CSS clamps it)'
	);
	ok(
		body.includes('line-clamp-2'),
		'the reduced mandate is CSS line-clamped, not an ellipsis or a truncation'
	);
	ok(body.includes('ignite · copy'), 'ignition is still reachable from the reduced row');
	ok(
		!body.includes('border-amber-800/60'),
		'the reduced mandate never wears the full bordered block'
	);
	// The item itself is not selected — its own fold (taken/needs/refs/body)
	// never mounts.
	ok(
		!body.includes('id="warp-item-the-loom-0:restructure"'),
		'the item fold stays unmounted while unselected'
	);
});

test('selecting an item shows the full bordered mandate block regardless of the layer fold', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [EMBER_ITEM, BANKED_ITEM],
				counts: { ember: 1, banked: 1, cold: 0, unstated: 0 }
			})
		],
		// The layer itself stays collapsed — selecting an item must reach
		// the full mandate on its own, without requiring the layer to open.
		initialOpenCallSign: null,
		initialOpenItemKey: '0:restructure'
	});
	ok(body.includes('The restructure'), 'the headline still renders');
	ok(body.includes('Implement the restructure.'), 'the full mandate text renders');
	ok(
		body.includes('border-amber-800/60'),
		'state 3 wears the full bordered block, same as the standing design'
	);
	ok(body.includes('ignite · copy'), 'ignition and copy stay reachable on the selected item');
	ok(body.includes('aria-expanded="true"'), 'the selected item row reports its open state');
	ok(
		body.includes('id="warp-item-the-loom-0:restructure"'),
		'the selected item mounts its own fold (taken/needs/refs/body)'
	);
});

test('an open band adds the held items; only an ember offers ignition', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [EMBER_ITEM, BANKED_ITEM],
				counts: { ember: 1, banked: 1, cold: 0, unstated: 0 }
			})
		],
		initialOpenCallSign: 'the-loom'
	});
	ok(body.includes('The restructure'));
	ok(body.includes('The past band'), 'the fold surfaces the banked item');
	ok(body.includes('ember'));
	ok(body.includes('banked'));
	// A held item's mandate stays context behind its own row fold, and never
	// wears the ignition affordance.
	const ignitions = body.split('ignite · copy').length - 1;
	ok(ignitions === 1, `only the ember offers ignition (saw ${ignitions})`);
});

test('a layer with no ember items renders its band with counts, nothing inline', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [BANKED_ITEM],
				counts: { ember: 0, banked: 1, cold: 0, unstated: 0 }
			})
		]
	});
	ok(body.includes('the-loom'));
	ok(body.includes('1 banked'));
	ok(!body.includes('The past band'), 'held items stay behind the fold');
	ok(!body.includes('ignite · copy'));
});

test('a bare warp renders the one quiet line', async () => {
	const body = await renderStack({ layers: [] });
	ok(body.includes('surface/layers/'));
});

test('a layer band offers its page link when the page supplies an opener (08-02: every layer is a page)', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [],
				counts: { ember: 0, banked: 0, cold: 0, unstated: 0 }
			})
		],
		onOpenPage: () => {}
	});
	ok(body.includes('page →'), 'the band carries the library link');
});

test('without an opener the band stays a pure disclosure — no dead control', async () => {
	const body = await renderStack({
		layers: [
			layer({
				items: [],
				counts: { ember: 0, banked: 0, cold: 0, unstated: 0 }
			})
		]
	});
	ok(!body.includes('page →'));
});
