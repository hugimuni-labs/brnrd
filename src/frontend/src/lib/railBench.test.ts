import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { environmentDisplay } from './railBench.ts';
import type { ConnectedRepo } from './repos.ts';

const source = readFileSync(new URL('./RailBench.svelte', import.meta.url), 'utf8');
const repo = { environment_default: 'host · default' } as ConnectedRepo;

// design-resident-field.md §"Settings, fuel, and the next dispatch": "press a
// provider row" opens the Bench already pointed at that provider — Resources
// (every observed meter) plus Next-run (SpoolRack, already tabbed there via
// `focusShell`). RailBench imports SpoolRack.svelte directly, which the
// RailGaugeRender/SpoolRackRender SSR-compile harness (a bare-node import of
// the compiled output, no bundler) cannot resolve — a `.svelte` import has no
// loader outside vite. Structural, source-level assertions are this file's
// own established pattern for exactly that reason (see the mobile-first and
// 44px-floor tests below); this test follows it rather than reaching for a
// harness this repo doesn't otherwise use.
test('a focused provider gets a Resources readout, conditionally rendered and wired to the rack', () => {
	assert.match(
		source,
		/\{#if resourceGroup\}/u,
		'Resources only renders once a provider is focused'
	);
	assert.ok(source.includes('data-measure="resources"'), 'the section carries its own measure');
	assert.match(
		source,
		/\{resourceGroup\.provider\}\s*·\s*resources/u,
		'the label names which provider is expanded'
	);
	assert.match(
		source,
		/resourceGroup\.meters as meter/u,
		'every observed meter renders, not only the primary'
	);
	// ONE CURSOR. `focusProvider` is not a hint the rack copies into its own
	// state — it *is* the bench's provider selection, and the rack's tabs
	// move it through `onProviderSelect` rather than moving a private twin.
	// The twin is what put a codex core list under a claude Resources
	// heading (reported 2026-08-28 with the screenshot).
	assert.match(
		source,
		/selectedShell=\{focusProvider\}/u,
		'the rack renders the bench cursor rather than seeding a copy of it'
	);
	assert.match(
		source,
		/onShellSelect=\{onProviderSelect\}/u,
		'and a tab tap moves that same cursor, so Resources cannot describe another provider'
	);
	assert.ok(!/focusShell=/u.test(source), 'the one-shot seeding prop is gone, not merely unused');
});

test('Resources renders each window as its own bar, adjacent to the cores the same cursor drives', () => {
	// The levels used to be inverted: the 12px collapsed row drew graphics
	// and this surface, with room to spare, drew text percentages. His own
	// note on the screenshot — "which if kept, should also itself be a
	// visual bar".
	assert.match(source, /class="resource-track"/u, 'every window gets a track');
	assert.match(source, /class="resource-fill"/u, 'and a fill measured on it');
	assert.match(
		source,
		/grid-template-areas:\s*\n?\s*'name track pct'/u,
		'name · bar · number share one grid, so the bars compare by length'
	);
	// Adjacency is the structural half of "one cursor": the provider's
	// readings sit directly above the provider's cores, with nothing
	// provider-independent wedged between them.
	const resources = source.indexOf('data-measure="resources"');
	const bays = source.indexOf('class="bench-bays');
	const rack = source.indexOf('class="spool-bay"');
	assert.ok(resources > bays, 'the provider block sits below the project/environment bays');
	assert.ok(rack > resources, 'and immediately above the core rack it belongs to');
});

test('a core-scope allowance is handed to the rack, not drawn on the shell bar', () => {
	// A `fable · week` window gates the fable core, never the whole claude
	// shell — so it renders on the row where that core is picked. On the
	// shell's fuel bar it was a third overlaid fill answering a question
	// nobody had asked yet.
	assert.match(source, /meter\.scope === 'core' && meter\.coreId !== null/u);
	assert.match(source, /\{coreAllowances\}/u, 'the rack receives them keyed by core');
});

test('the resolved default renders its name and the default badge as two separate facts', () => {
	const display = environmentDisplay(repo, null);
	assert.equal(display.name, 'host · default');
	assert.equal(display.isDefault, true);
});

test('an explicit selection is never marked as the default badge', () => {
	const display = environmentDisplay(repo, 'staging');
	assert.equal(display.name, 'staging');
	assert.equal(display.isDefault, false);
});

test('no repo policy at all renders honestly, not as a fabricated default', () => {
	const display = environmentDisplay(null, null);
	assert.equal(display.isDefault, false);
	assert.equal(display.name, 'no environment configured');
});

test('the bench is mobile-first: bays stack compactly before widening at md', () => {
	assert.match(source, /grid gap-3 md:grid-cols-2/);
	assert.doesNotMatch(source, /class="panel[^"]*"/);
});

test('every bench pick and inherited rack control has a 44px floor', () => {
	assert.equal((source.match(/data-role="bench-pick"/g) ?? []).length, 3);
	assert.match(source, /\.pick-row\s*\{\s*min-height:\s*44px;?\s*\}/);
	assert.match(source, /button\[role='tab'\][\s\S]*min-height: 44px/);
	assert.match(source, /button\[data-role='rack-row-tap'\][\s\S]*min-height: 44px/);
});

test('mobile labels name the choices without ornamental bay numbers or rails', () => {
	assert.match(source, /class="workshop-label">project/);
	assert.match(source, /class="workshop-label">environment/);
	assert.doesNotMatch(source, /<span>0[123]<\/span>/);
});
