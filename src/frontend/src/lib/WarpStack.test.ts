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
// the band. Same server-side render dance as BackchannelQueue's tests:
// compile with stubbed children, assert on the produced markup.
async function renderStack(props: {
	layers: WarpLayer[];
	initialOpenCallSign?: string | null;
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
	bodyMarkdown: 'Body.'
};

test('a folded band shows call sign, heat counts, and its ember items inline — no click needed', async () => {
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
	ok(body.includes('The restructure'), 'ember item renders inline on the folded band');
	ok(
		body.includes('Implement the restructure.'),
		'the ember prompt affordance renders inline on the folded band'
	);
	ok(body.includes('ignite · copy'), 'the ignition affordance rides the standing row');
	// The held remainder stays behind the fold.
	ok(!body.includes('The past band'), 'banked item leaks out of the folded band');
	ok(!body.includes('Build the cloth window.'), 'banked prompt leaks out of the folded band');
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
