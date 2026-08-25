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
	assert.match(
		source,
		/focusShell=\{focusProvider\}/u,
		"the same tap opens the rack already tabbed to that provider — one 'press a provider row' outcome, not two disconnected pickers"
	);
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
