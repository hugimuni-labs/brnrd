import assert from 'node:assert/strict';
import test from 'node:test';

import { buildCrossingIndex, crossingCells, crossingThreads } from './crossing.ts';
import { THREAD_SCALE, threadColor } from './statusPalette.ts';
import type { WarpLayer } from './warp.ts';

function layer(callSign: string, items: { headline: string; taken: string[] }[]): WarpLayer {
	return {
		callSign,
		path: `surface/layers/${callSign}.md`,
		definitionMarkdown: '',
		items: items.map((item, index) => ({
			key: `${callSign}-${index}`,
			headline: item.headline,
			kind: null,
			state: null,
			needs: null,
			refs: [],
			prompt: null,
			taken: item.taken,
			bodyMarkdown: ''
		})),
		counts: { ember: 0, banked: 0, cold: 0, unstated: 0 }
	} as WarpLayer;
}

const LAYERS = [
	layer('the-loom', [
		{ headline: 'the machine block', taken: ['run-a', 'run-b'] },
		{ headline: 'the weld', taken: ['run-a'] }
	]),
	layer('the-post', [{ headline: 'the graveyard', taken: ['run-b'] }]),
	layer('the-clockwork', [{ headline: 'the suspend timebomb', taken: [] }])
];

test('the threads are the warp layers, in authored order', () => {
	assert.deepEqual(crossingThreads(LAYERS), ['the-loom', 'the-post', 'the-clockwork']);
	assert.deepEqual(crossingThreads([]), []);
});

test('a run that lifted two items on one layer crossed that thread once', () => {
	assert.deepEqual(buildCrossingIndex(LAYERS).get('run-a'), ['the-loom']);
});

test('a crossing spans every layer the run touched, in thread order', () => {
	assert.deepEqual(buildCrossingIndex(LAYERS).get('run-b'), ['the-loom', 'the-post']);
});

test('the index reads finished runs too — the strip has to draw in both tenses', () => {
	// Nothing here is filtered by liveness: `taken:` outlives the run, which is
	// what lets a cloth line three days old still name its threads.
	const index = buildCrossingIndex(LAYERS);
	assert.equal(index.size, 2);
	assert.equal(index.has('run-a'), true);
});

test('a strip lights exactly the threads crossed and dims the rest', () => {
	const cells = crossingCells(crossingThreads(LAYERS), ['the-post']);
	assert.deepEqual(
		cells.map((cell) => [cell.callSign, cell.lit]),
		[
			['the-loom', false],
			['the-post', true],
			['the-clockwork', false]
		]
	);
});

test('an unwelded run draws no strip at all — not a row of dark ticks', () => {
	// A strip of all-dark ticks would claim "this run crossed nothing", which
	// is a different fact from "the warp has never heard of this run", and the
	// wire cannot tell them apart.
	assert.deepEqual(crossingCells(crossingThreads(LAYERS), undefined), []);
	assert.deepEqual(crossingCells(crossingThreads(LAYERS), []), []);
	assert.deepEqual(crossingCells([], ['the-loom']), []);
});

// Colour is identity, not magnitude (THREAD_SCALE). It exists because the first
// cut carried identity in position alone, which nobody can read without
// hovering — his read: "nice to see which one(s) is / are being worked … but
// the current version doesn't convey that correctly."

test('a thread wears the hue of its place in the authored order', () => {
	const cells = crossingCells(crossingThreads(LAYERS), ['the-post']);
	assert.deepEqual(
		cells.map((cell) => cell.color),
		[threadColor(0), threadColor(1), threadColor(2)]
	);
});

test('the hue rides the cell, so a legend and a strip cannot drift apart', () => {
	// Both surfaces call `crossingCells` with the same thread order, so the
	// colour is decided once rather than looked up twice by two renderers.
	const legend = crossingCells(crossingThreads(LAYERS), crossingThreads(LAYERS));
	const strip = crossingCells(crossingThreads(LAYERS), ['the-post']);
	assert.deepEqual(
		legend.map((c) => c.color),
		strip.map((c) => c.color)
	);
});

test('a ninth thread reuses the first hue rather than inventing one', () => {
	// A generated colour would be a hue nobody chose; the order still
	// disambiguates, and wrapping is the honest failure.
	assert.equal(threadColor(THREAD_SCALE.length), THREAD_SCALE[0]);
	assert.equal(threadColor(THREAD_SCALE.length + 2), THREAD_SCALE[2]);
});
