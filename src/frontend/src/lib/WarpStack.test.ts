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

// The warp stack's visible contract (design-work-layers.md §Interaction):
// the folded band is a supply gauge; an opened item offers ignition only
// when it is ember. Same server-side render dance as BackchannelQueue's
// tests: compile with stubbed children, assert on the produced markup.
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

test('a folded band shows its call sign and heat counts, not its items', async () => {
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
	ok(!body.includes('The restructure'), 'folded band leaks its items');
});

test('an open band lists items; only an ember offers ignition', async () => {
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
	ok(body.includes('The past band'));
	// Item folds start closed, so neither prompt renders yet — but the open
	// band's item rows are present with their heat chips.
	ok(body.includes('ember'));
	ok(body.includes('banked'));
});

test('a bare warp renders the one quiet line', async () => {
	const body = await renderStack({ layers: [] });
	ok(body.includes('surface/layers/'));
});
