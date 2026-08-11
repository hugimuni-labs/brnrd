import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { ConfigChangeRequestItem } from './configRequests.ts';
import type { PRReviewItem } from './prReviewQueue.ts';
import type { WithheldLane } from './withheld.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'BackchannelQueue.svelte');
const generated = join(here, '.backchannelQueue.generated.mjs');

// The needs-you strip's derived half only (2026-08-11: the resident-authored
// half retired into the warp, and this component's authored-item fold went
// with it — nothing produces one in production anymore). These tests render
// the real component server-side (same dance as workSurfaceHeader.test.ts:
// compile with a stubbed child component, since `WithheldNotice` doesn't
// compile standalone outside a bundler's `.svelte` resolution) and assert
// the derived rows, the stale badge, the empty state, and the draft
// footnote on the produced markup.
async function renderQueue(props: {
	prs?: PRReviewItem[];
	requests?: ConfigChangeRequestItem[];
	stale?: boolean;
	withheld?: WithheldLane | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'BackchannelQueue'
	});
	const runnable = compiled.js.code
		.replace(
			/import\s+WithheldNotice\s+from\s*'\.\/WithheldNotice\.svelte';/,
			'const WithheldNotice = () => {};'
		)
		.replace(/'\.\/backchannel'/g, "'./backchannel.ts'")
		.replace(/'\.\/prReviewQueue'/g, "'./prReviewQueue.ts'")
		.replace(/'\.\/runLedger'/g, "'./runLedger.ts'")
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

function pr(overrides: Partial<PRReviewItem>): PRReviewItem {
	return {
		number: 1,
		title: 'a title',
		url: 'https://example.test/pr/1',
		repo_label: 'x/y',
		created_at: null,
		draft: false,
		author: '',
		...overrides
	};
}

test('nothing waiting renders the honest empty state, no withheld notice', async () => {
	const html = await renderQueue({});
	ok(html.includes('Nothing needs you right now.'));
});

test('a withheld lane takes over the empty state instead of the plain message', async () => {
	const html = await renderQueue({
		withheld: { lane: 'needs-you', unrecorded: ['x/y'] } satisfies WithheldLane
	});
	ok(!html.includes('Nothing needs you right now.'));
});

test('a draft PR renders no row — the empty state holds even with one PR present', async () => {
	const html = await renderQueue({ prs: [pr({ draft: true, title: 'still cooking' })] });
	ok(html.includes('Nothing needs you right now.'));
	ok(!html.includes('still cooking'));
});

test('a non-draft PR renders its row', async () => {
	const html = await renderQueue({ prs: [pr({ number: 7, title: 'ship it', draft: false })] });
	ok(html.includes('#7 ship it'));
	ok(!html.includes('Nothing needs you right now.'));
});

test('a mixed batch renders only the non-draft row plus a quiet draft footnote', async () => {
	const html = await renderQueue({
		prs: [
			pr({ number: 1, title: 'ready for review', draft: false }),
			pr({ number: 2, title: 'wip', draft: true }),
			pr({ number: 3, title: 'also wip', draft: true })
		]
	});
	ok(html.includes('#1 ready for review'));
	ok(!html.includes('wip'));
	ok(html.includes('2 draft, still being worked'));
});

test('no draft footnote when nothing is withheld', async () => {
	const html = await renderQueue({ prs: [pr({ number: 1, draft: false })] });
	ok(!html.includes('draft, still being worked'));
});

test('the stale badge renders only when the report is stale', async () => {
	const stale = await renderQueue({ prs: [pr({ draft: false })], stale: true });
	ok(stale.includes('stale report'));
	const fresh = await renderQueue({ prs: [pr({ draft: false })], stale: false });
	ok(!fresh.includes('stale report'));
});

test('the naming remnant is gone — no "backchannel" wording renders', async () => {
	const html = await renderQueue({ prs: [pr({ draft: false })], stale: true });
	ok(!/backchannel/i.test(html));
});
