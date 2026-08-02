import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { AuthoredBackchannelItem } from './backchannelPage.ts';
import type { PRReviewItem } from './prReviewQueue.ts';
import type { WarpLayer } from './warp.ts';

const here = dirname(fileURLToPath(import.meta.url));

// The warp band is the flip's grave (2026-08-02): the layer stack is the
// standing body and renders ALWAYS — the old heddle default replaced it with
// the needs-you list whenever items waited, so a daemon restart that
// resolved the feeds made the warp visually vanish behind a tab. These
// tests render the real composition (WarpBand with the real WarpStack and
// BackchannelQueue compiled in, leaf children stubbed) and pin: the stack
// survives a pending queue; the strip is collapsed by default with its
// count and top asks; expansion adds the full list without removing the
// stack.

const STACK_GEN = '.warpBand.warpStack.generated.mjs';
const QUEUE_GEN = '.warpBand.backchannelQueue.generated.mjs';
const BAND_GEN = '.warpBand.generated.mjs';
const generatedFiles = [STACK_GEN, QUEUE_GEN, BAND_GEN].map((name) => join(here, name));

function compileTo(name: string, sourceFile: string, outName: string): void {
	const source = readFileSync(join(here, sourceFile), 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name });
	const runnable = compiled.js.code
		.replace(
			/import\s+MarkdownContent\s+from\s*'\.\/MarkdownContent\.svelte';/,
			'const MarkdownContent = () => {};'
		)
		.replace(
			/import\s+WithheldNotice\s+from\s*'\.\/WithheldNotice\.svelte';/,
			'const WithheldNotice = () => {};'
		)
		.replace(
			/import\s+WarpStack\s+from\s*'\.\/WarpStack\.svelte';/,
			`import WarpStack from './${STACK_GEN}';`
		)
		.replace(
			/import\s+BackchannelQueue\s+from\s*'\.\/BackchannelQueue\.svelte';/,
			`import BackchannelQueue from './${QUEUE_GEN}';`
		)
		.replace(/'\.\/backchannel'/g, "'./backchannel.ts'")
		.replace(/'\.\/backchannelPage'/g, "'./backchannelPage.ts'")
		.replace(/'\.\/configRequests'/g, "'./configRequests.ts'")
		.replace(/'\.\/prReviewQueue'/g, "'./prReviewQueue.ts'")
		.replace(/'\.\/statusPalette'/g, "'./statusPalette.ts'")
		.replace(/'\.\/warp'/g, "'./warp.ts'")
		.replace(/'\.\/withheld'/g, "'./withheld.ts'");
	writeFileSync(join(here, outName), runnable);
}

interface BandProps {
	surfaceLoaded?: boolean;
	layers?: WarpLayer[];
	authoredItems?: AuthoredBackchannelItem[];
	prs?: PRReviewItem[] | null;
	feedsResolved?: boolean;
	initialNeedsOpen?: boolean;
}

async function renderBand(props: BandProps): Promise<string> {
	compileTo('WarpStack', 'WarpStack.svelte', STACK_GEN);
	compileTo('BackchannelQueue', 'BackchannelQueue.svelte', QUEUE_GEN);
	compileTo('WarpBand', 'WarpBand.svelte', BAND_GEN);
	try {
		const module = await import(`./${BAND_GEN}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				surfaceLoaded: true,
				layers: [],
				prs: [],
				requests: [],
				feedsResolved: true,
				stale: false,
				now: Date.parse('2026-08-02T12:00:00Z'),
				...props
			}
		}).body;
	} finally {
		for (const file of generatedFiles) rmSync(file, { force: true });
	}
}

after(() => {
	for (const file of generatedFiles) rmSync(file, { force: true });
});

function authored(overrides: Partial<AuthoredBackchannelItem>): AuthoredBackchannelItem {
	return {
		key: '0:untitled',
		headline: 'untitled',
		kind: null,
		state: null,
		needs: null,
		refs: [],
		prompt: null,
		bodyMarkdown: '',
		...overrides
	};
}

const LOOM_LAYER: WarpLayer = {
	callSign: 'the-loom',
	path: 'surface/layers/the-loom.md',
	definitionMarkdown: 'The redesign band.',
	items: [
		{
			key: '0:restructure',
			headline: 'The warp restructure',
			kind: null,
			state: 'ember',
			needs: null,
			refs: [],
			prompt: 'Implement the restructure.',
			bodyMarkdown: ''
		}
	],
	counts: { ember: 1, banked: 0, cold: 0, unstated: 0 }
};

const DECIDE_ITEM = authored({
	key: '0:decide-the-split',
	headline: 'decide the split',
	kind: 'decide',
	bodyMarkdown: 'Context prose.'
});

const PR_ITEM: PRReviewItem = {
	number: 915,
	title: 'the weld',
	url: 'https://example.test/pr/915',
	repo_label: 'hugimuni-labs/brnrd',
	created_at: '2026-08-01T10:00:00Z',
	draft: false,
	author: 'brnrd'
};

test('the regression: the stack renders even while items pend and every feed is resolved', async () => {
	const html = await renderBand({
		layers: [LOOM_LAYER],
		authoredItems: [DECIDE_ITEM],
		prs: [PR_ITEM],
		feedsResolved: true
	});
	// The old flip: this exact state (pending > 0, feeds resolved) defaulted
	// the section body to the needs list and the layer stack vanished.
	ok(html.includes('the-loom'), 'the layer stack must render alongside a pending queue');
	ok(html.includes('The warp restructure'), 'the ember items render with it');
	ok(html.includes('needs you'), 'the strip renders above it');
	ok(html.includes('1 authored · 1 derived'), 'the strip chip attributes the count');
});

test('the strip is collapsed by default: count and top asks only, no full queue', async () => {
	const html = await renderBand({
		layers: [LOOM_LAYER],
		authoredItems: [DECIDE_ITEM],
		prs: [PR_ITEM],
		feedsResolved: true
	});
	ok(html.includes('aria-controls="warp-needs-fold"'), 'the strip declares its fold');
	ok(!html.includes('id="warp-needs-fold"'), 'the fold body is not mounted while collapsed');
	ok(html.includes('decide the split'), 'the top ask headline previews on the collapsed strip');
	ok(html.includes('>decide<'), 'the top ask wears its kind chip');
	ok(!html.includes('resident backchannel'), 'the full queue stays behind the fold');
	ok(!html.includes('derived — forge &amp; config'), 'the derived sub-lens stays behind the fold');
});

test('the expanded strip holds the full list — authored and derived sub-lenses — and the stack stays', async () => {
	const html = await renderBand({
		layers: [LOOM_LAYER],
		authoredItems: [DECIDE_ITEM],
		prs: [PR_ITEM],
		feedsResolved: true,
		initialNeedsOpen: true
	});
	ok(html.includes('id="warp-needs-fold"'), 'the fold body mounts');
	ok(html.includes('resident backchannel'), 'the full queue renders in place');
	ok(html.includes('derived — forge &amp; config'), 'the derived sub-lens keeps its section');
	ok(html.includes('#915 the weld'), 'derived rows keep their content');
	ok(html.includes('the-loom'), 'expanding the strip never removes the stack');
	ok(html.includes('The warp restructure'), 'the ember items stay inline below');
});

test('a decision/action ask outranks an earlier review item in the strip preview', async () => {
	const html = await renderBand({
		layers: [],
		authoredItems: [
			authored({ key: '0:read-the-pr', headline: 'read the weld PR', kind: 'review' }),
			authored({ key: '1:approve-the-cut', headline: 'approve the cut', kind: 'act' })
		],
		feedsResolved: true
	});
	const ask = html.indexOf('approve the cut');
	const review = html.indexOf('read the weld PR');
	ok(ask !== -1 && review !== -1, 'both preview rows render');
	ok(ask < review, 'the act item leads the preview despite coming later in the file');
});

test('feed state touches only the strip chip — an unresolved count never hides the stack', async () => {
	const html = await renderBand({
		layers: [LOOM_LAYER],
		authoredItems: [],
		prs: null,
		feedsResolved: false
	});
	ok(html.includes('counting…'), 'the chip says counting while feeds are unresolved');
	ok(html.includes('the-loom'), 'the stack stands regardless');
});

test('an empty warp keeps its bare line under the strip', async () => {
	const html = await renderBand({ layers: [], feedsResolved: true });
	ok(html.includes('the warp is bare'));
	ok(html.includes('needs you'));
});
