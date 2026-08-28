import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { environmentDisplay } from './railBench.ts';
import type { ConnectedRepo } from './repos.ts';

const source = readFileSync(new URL('./RailBench.svelte', import.meta.url), 'utf8');
const repo = { environment_default: 'host · default' } as ConnectedRepo;

// RailBench is the settings block now: project · environment, and nothing
// else. It held four things under one heading — project, environment, a
// provider's Resources, and a CLAUDE|CODEX core picker — two of which
// belonged to a provider and two of which did not, which is why the panel
// never read as one object. The provider half moved to `ProviderBay`,
// opened by pressing that provider's fuel row (maintainer, 2026-08-28).
const bayPath = new URL('./ProviderBay.svelte', import.meta.url);
const bay = readFileSync(bayPath, 'utf8');

test('settings holds where the work happens, and nothing about which body runs it', () => {
	assert.ok(source.includes('data-measure="settings"'), 'the block names itself for what it is');
	assert.ok(source.includes('data-measure="project"'));
	assert.ok(source.includes('data-measure="environment"'));
	assert.ok(!source.includes('SpoolRack'), 'the core picker is not here');
	assert.ok(!source.includes('data-measure="resources"'), 'and neither are a provider’s windows');
	assert.ok(!/focusProvider|onProviderSelect/u.test(source), 'no provider cursor passes through');
});

test('the place a wake lands is announced, never reached across for', () => {
	// The settings block does not know what the rack does with the pair; it
	// raises the change and the page hands it on. One-way, so the two blocks
	// can move apart on the page without either learning about the other.
	assert.match(source, /onPlaceChange\?\.\(/u);
	assert.match(source, /function selectEnvironment/u, 'every environment pick announces');
});

test('the provider bay carries the windows as bars, beside the cores of that one shell', () => {
	assert.ok(bay.includes('data-measure="resources"'), 'the windows moved here');
	assert.match(bay, /class="resource-track"/u, 'every window gets a track');
	assert.match(bay, /class="resource-fill"/u, 'and a fill measured on it');
	assert.match(
		bay,
		/grid-template-areas:\s*\n?\s*'name track pct'/u,
		'name · bar · number share one grid, so the bars compare by length'
	);
	// Adjacency is the structural half: one provider's readings and one
	// provider's cores, in one component, with nothing provider-independent
	// wedged between them.
	const resources = bay.indexOf('data-measure="resources"');
	const rack = bay.indexOf('<SpoolRack');
	assert.ok(resources > 0 && rack > resources, 'the windows sit directly above the cores');
	assert.match(bay, /shell=\{group\.provider\}/u, 'the rack is told its shell, not asked to pick');
	assert.ok(!/role="tab"/u.test(bay), 'and there is no strip to pick a different one');
});

test('a core-scope allowance is handed to the rack, not drawn on the shell bar', () => {
	// A `fable · week` window gates the fable core, never the whole claude
	// shell — so it renders on the row where that core is picked.
	assert.match(bay, /meter\.scope === 'core' && meter\.coreId !== null/u);
	assert.match(bay, /\{coreAllowances\}/u, 'the rack receives them keyed by core');
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

test('every settings pick and every rack row keeps its 44px floor', () => {
	assert.equal((source.match(/data-role="bench-pick"/g) ?? []).length, 3);
	assert.match(source, /\.pick-row\s*\{\s*min-height:\s*44px;?\s*\}/);
	// The tab rule went with the tabs. The rack-row floor followed the rack
	// into the provider bay rather than being dropped.
	assert.ok(!/button\[role='tab'\]/u.test(source), 'no tab rule survives the tab strip');
	assert.match(bay, /button\[data-role='rack-row-tap'\][\s\S]*min-height: 44px/);
});

test('THE UNREACHABLE FLOOR: the gauge rows are controls that cannot meet 44px', () => {
	// Named, not silently resolved. Making a provider row pressable turned a
	// readout into this surface's primary control — and the 44px floor this
	// file has enforced since 2026-08-19 cannot reach it, because two rules
	// the maintainer signed now contradict each other:
	//
	//   `.fuel-deck` is 85px, fixed, `overflow-y: auto`, with its own
	//   acceptance test (RailGaugeRender) pinning that twelve providers stay
	//   twelve rows. Interior box ≈ 76px. Two rows at 44px need 92px.
	//
	// The row is 34px and full-bleed, so it is a far larger target than a
	// 44px square — but that is an argument, not the rule as written. The
	// options are: accept full-width height as the floor's real intent for a
	// row (cheap, needs saying out loud), raise the deck and re-sign the
	// fixed-height number (touches a signed acceptance), or scroll two
	// providers in one viewport (worst — it hides codex behind a gesture).
	// This test exists so the conflict cannot be rediscovered as a surprise.
	const gauge = readFileSync(new URL('./RailGauge.svelte', import.meta.url), 'utf8');
	assert.match(gauge, /grid-template-rows: 14px 12px 8px/u, 'the row is 34px of grid');
	assert.match(gauge, /height: 85px/u, 'inside a deck that may not grow');
	assert.match(gauge, /class="fuel-provider-row"/u);
	assert.match(gauge, /aria-expanded=\{open\}/u, 'and it is a real control, not a readout');
});

test('mobile labels name the choices without ornamental bay numbers or rails', () => {
	assert.match(source, /class="workshop-label">project/);
	assert.match(source, /class="workshop-label">environment/);
	assert.doesNotMatch(source, /<span>0[123]<\/span>/);
});
