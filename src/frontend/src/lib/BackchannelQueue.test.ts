import { equal, ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { AuthoredBackchannelItem } from './backchannelPage.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'BackchannelQueue.svelte');
const generated = join(here, '.backchannelQueue.generated.mjs');

// The briefing fold, compacted one level further (design-dashboard-briefing
// §3, then the 2026-08-01 list-itself compaction): a folded authored item's
// row carries only its disclosure, headline, and kind chip. Refs and the
// `prompt:` copy-chip move inside the fold body, above the prose, and render
// only once that one row is open. These tests render the real component
// server-side (same dance as workSurfaceHeader.test.ts: compile with stubbed
// child components, since neither `MarkdownContent` nor `WithheldNotice`
// compiles standalone outside a bundler's `.svelte` resolution) and assert
// the fold's contract on the produced markup.
async function renderQueue(props: {
	authoredItems?: AuthoredBackchannelItem[];
	initialOpenKey?: string | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'BackchannelQueue'
	});
	const runnable = compiled.js.code
		.replace(
			/import\s+MarkdownContent\s+from\s*'\.\/MarkdownContent\.svelte';/,
			'const MarkdownContent = () => {};'
		)
		.replace(
			/import\s+WithheldNotice\s+from\s*'\.\/WithheldNotice\.svelte';/,
			'const WithheldNotice = () => {};'
		)
		.replace(/'\.\/backchannel'/g, "'./backchannel.ts'")
		.replace(/'\.\/prReviewQueue'/g, "'./prReviewQueue.ts'")
		.replace(/'\.\/statusPalette'/g, "'./statusPalette.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				prs: [],
				requests: [],
				stale: false,
				now: Date.parse('2026-08-01T12:00:00Z'),
				...props
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function item(overrides: Partial<AuthoredBackchannelItem>): AuthoredBackchannelItem {
	return {
		key: '0:untitled',
		headline: 'untitled',
		kind: null,
		refs: [],
		prompt: null,
		bodyMarkdown: '',
		...overrides
	};
}

const decideTheSplit = item({
	key: '0:decide-the-split',
	headline: 'decide the split',
	kind: 'decide',
	refs: [
		{ label: 'design-wyrd', href: null },
		{ label: 'PR #915', href: 'https://example.test/pr/915' }
	],
	prompt: 'answer the split question in one line',
	bodyMarkdown: 'Two paragraphs of context the row must not read aloud.'
});

const readTheLedger = item({
	key: '1:read-the-ledger',
	headline: 'read the ledger',
	kind: 'read',
	prompt: null,
	bodyMarkdown: 'The ledger prose.'
});

test('a folded row renders only the headline and kind chip — no refs, no prompt chip', async () => {
	const html = await renderQueue({ authoredItems: [decideTheSplit] });
	ok(html.includes('decide the split'));
	ok(html.includes('>decide<'), 'kind chip renders its label');
	ok(!html.includes('design-wyrd'), 'refs stay folded behind the row');
	ok(!html.includes('https://example.test/pr/915'), 'link refs stay folded behind the row');
	ok(
		!html.includes('answer the split question in one line'),
		'the prompt chip stays folded behind the row'
	);
});

test('opening a row surfaces its refs, its prompt chip, and its prose', async () => {
	const html = await renderQueue({
		authoredItems: [decideTheSplit],
		initialOpenKey: '0:decide-the-split'
	});
	ok(html.includes('decide the split'));
	ok(html.includes('design-wyrd'), 'bare ref renders as a label once open');
	ok(html.includes('https://example.test/pr/915'), 'link ref renders as a link once open');
	ok(html.includes('answer the split question in one line'), 'the prompt chip renders once open');
	ok(
		html.includes('id="backchannel-fold-0:decide-the-split"'),
		'the fold body (refs, prompt chip, and — via MarkdownContent, stubbed here — the prose) is mounted once open'
	);
});

test('the body folds behind the row by default — closed rows render no body container, no refs, no prompt chip', async () => {
	const html = await renderQueue({ authoredItems: [decideTheSplit, readTheLedger] });
	ok(!html.includes('aria-expanded="true"'));
	ok(html.includes('aria-expanded="false"'), 'a folded row still declares the fold');
	ok(!html.includes('id="backchannel-fold-'), 'no fold body is mounted while closed');
	ok(!html.includes('design-wyrd'), 'refs stay folded');
	ok(!html.includes('answer the split question in one line'), 'the prompt chip stays folded');
});

test('exactly one row unfolds — the open key mounts that body and only that body', async () => {
	const html = await renderQueue({
		authoredItems: [decideTheSplit, readTheLedger],
		initialOpenKey: '1:read-the-ledger'
	});
	equal(html.split('aria-expanded="true"').length - 1, 1, 'one open row');
	ok(html.includes('id="backchannel-fold-1:read-the-ledger"'));
	ok(!html.includes('id="backchannel-fold-0:decide-the-split"'));
});

test('an item with no body offers no fold affordance', async () => {
	const html = await renderQueue({
		authoredItems: [item({ key: '0:no-body', headline: 'no body here', kind: 'act' })]
	});
	ok(html.includes('no body here'));
	ok(!html.includes('aria-expanded'), 'nothing to unfold, so no expand control');
});

test('an item with no body has nowhere to fold refs and the prompt chip into, so they stay on the row', async () => {
	const html = await renderQueue({
		authoredItems: [
			item({
				key: '0:no-body',
				headline: 'no body here',
				kind: 'act',
				refs: [{ label: 'design-wyrd', href: null }],
				prompt: 'do the thing'
			})
		]
	});
	ok(html.includes('no body here'));
	ok(html.includes('design-wyrd'), 'refs render on an un-foldable row');
	ok(html.includes('do the thing'), 'the prompt chip renders on an un-foldable row');
});
