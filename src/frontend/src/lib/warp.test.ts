import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseBackchannelPage } from './backchannelPage.ts';
import {
	buildWarpLayers,
	emberCount,
	ignitionPayload,
	isLayerFile,
	itemRepos,
	layerCallSign,
	layerDefinition,
	warpRepos
} from './warp.ts';
import type { SurfaceFile } from './surface.ts';

// ── the grammar extension (state:/needs: rows) ─────────────────────────────

test('state and needs rows parse as schema, not body', () => {
	const markdown = `## The loom-page restructure

state: ember
needs: nothing — dispatchable
refs: [#972](https://example.test/972)
prompt: Implement the restructure.

The sentence, his nod on it.
`;
	const [item] = parseBackchannelPage(markdown);
	assert.equal(item.state, 'ember');
	assert.equal(item.needs, 'nothing — dispatchable');
	assert.equal(item.prompt, 'Implement the restructure.');
	assert.equal(item.bodyMarkdown, 'The sentence, his nod on it.');
});

test('all three heats parse; case is normalized', () => {
	for (const heat of ['ember', 'banked', 'cold'] as const) {
		const [item] = parseBackchannelPage(`## X\n\nstate: ${heat.toUpperCase()}\n\nBody.\n`);
		assert.equal(item.state, heat);
	}
});

test('an unknown state value is dropped, not leaked into the body', () => {
	const [item] = parseBackchannelPage(`## X\n\nstate: glowing\n\nBody.\n`);
	assert.equal(item.state, null);
	assert.equal(item.bodyMarkdown, 'Body.');
});

test('an item without a state row reports null, distinct from authored cold', () => {
	const [unstated] = parseBackchannelPage(`## X\n\nkind: act\n\nBody.\n`);
	assert.equal(unstated.state, null);
	const [cold] = parseBackchannelPage(`## X\n\nstate: cold\n\nBody.\n`);
	assert.equal(cold.state, 'cold');
});

// ── layer discovery ────────────────────────────────────────────────────────

test('isLayerFile accepts only direct markdown children of surface/layers/', () => {
	assert.equal(isLayerFile('surface/layers/the-loom.md'), true);
	assert.equal(isLayerFile('surface/layers/index.md'), false);
	assert.equal(isLayerFile('surface/layers/nested/deep.md'), false);
	assert.equal(isLayerFile('surface/backchannel.md'), false);
	assert.equal(isLayerFile('knowledge/layers/foo.md'), false);
	assert.equal(isLayerFile('surface/layers/notes.txt'), false);
});

test('layerCallSign is the basename without extension', () => {
	assert.equal(layerCallSign('surface/layers/the-loom.md'), 'the-loom');
	assert.equal(layerCallSign('surface/layers/adoption.md'), 'adoption');
});

test('layerDefinition keeps the preamble prose and drops the title line', () => {
	const markdown = `# the-loom — the dashboard becomes the machine

The redesign band: everything that turns the dashboard into the loom.
Second definition line.

## First item

state: ember

Body.
`;
	assert.equal(
		layerDefinition(markdown),
		'The redesign band: everything that turns the dashboard into the loom.\nSecond definition line.'
	);
});

test('layerDefinition of a page that opens straight into items is empty', () => {
	assert.equal(layerDefinition('## Item one\n\nstate: cold\n'), '');
});

// ── the stack ──────────────────────────────────────────────────────────────

function file(path: string, markdown: string): SurfaceFile {
	return { path, markdown };
}

const THE_LOOM = `# the-loom

The redesign band.

## Restructure

state: ember
prompt: Do the restructure.

Body.

## Past band

state: banked
needs: the restructure landing first

Body.

## A thing nobody heated

Body.
`;

const LEGAL = `# legal

The compliance band.

## ToS exit

state: cold

Body.
`;

test('buildWarpLayers discovers layer files, counts heat, and keeps file order', () => {
	const layers = buildWarpLayers([
		file('surface/index.md', '# Work surface'),
		file('surface/layers/legal.md', LEGAL),
		file('surface/layers/the-loom.md', THE_LOOM),
		file('surface/backchannel.md', '## Not a layer item\n\nBody.\n')
	]);
	assert.deepEqual(
		layers.map((l) => l.callSign),
		['legal', 'the-loom']
	);
	const loom = layers[1];
	assert.equal(loom.path, 'surface/layers/the-loom.md');
	assert.equal(loom.definitionMarkdown, 'The redesign band.');
	assert.equal(loom.items.length, 3);
	assert.deepEqual(loom.counts, { ember: 1, banked: 1, cold: 0, unstated: 1 });
	assert.deepEqual(layers[0].counts, { ember: 0, banked: 0, cold: 1, unstated: 0 });
});

test('emberCount sums the dispatchable draw across the warp', () => {
	const layers = buildWarpLayers([
		file('surface/layers/the-loom.md', THE_LOOM),
		file('surface/layers/legal.md', LEGAL)
	]);
	assert.equal(emberCount(layers), 1);
});

test('a bare warp is an empty array, not an error', () => {
	assert.deepEqual(buildWarpLayers([file('surface/index.md', '# hi')]), []);
});

// ── ignition payload: the copied prompt carries the item's address ─────────

test('the copied ignition payload ends with the correctly-slugged item address', () => {
	const [item] = parseBackchannelPage(
		`## Kill "THE FLIP" — the warp stands!

state: ember
prompt: Implement the restructure.

Body.
`
	);
	const payload = ignitionPayload('the-loom', item);
	// The address is the last line — the daemon scans ignition event bodies
	// for `layer#slug` to annotate the item and weld run relics onto its
	// refs; slug = headingAnchor over the heading text, punctuation dropped.
	assert.ok(payload.endsWith('\n\nitem: the-loom#kill-the-flip-the-warp-stands'));
	assert.ok(payload.startsWith('Implement the restructure.'));
});

// ── multi-repo: repos derived from refs, never declared ────────────────────

test('itemRepos derives the repo set from qualified shorthands and forge hrefs', () => {
	const [item] = parseBackchannelPage(
		`## Cross-repo item

state: ember
refs: hugimuni-labs/brnrd#928 · [#12](https://github.com/other-org/site/pull/12) · subject-daemon.md · hugimuni-labs/brnrd#929

Body.
`
	);
	// Deduplicated, first-mention order; the kb-page ref names no repo.
	assert.deepEqual(itemRepos(item), ['hugimuni-labs/brnrd', 'other-org/site']);
});

test('itemRepos names no repo for kb pages, bare #N, and non-repo forge urls', () => {
	const [item] = parseBackchannelPage(
		`## Surface-only item

refs: #928 · workflow.md §Gating · [orgs](https://github.com/orgs/hugimuni-labs/people)

Body.
`
	);
	assert.deepEqual(itemRepos(item), []);
});

test('warpRepos is the deduplicated union across layers — the repo heddle option set', () => {
	const layers = buildWarpLayers([
		file('surface/layers/a.md', '# a\n\nD.\n\n## One\n\nrefs: hugimuni-labs/brnrd#1\n\nB.\n'),
		file(
			'surface/layers/b.md',
			'# b\n\nD.\n\n## Two\n\nrefs: other-org/site#2 · hugimuni-labs/brnrd#3\n\nB.\n'
		)
	]);
	assert.deepEqual(warpRepos(layers), ['hugimuni-labs/brnrd', 'other-org/site']);
});
