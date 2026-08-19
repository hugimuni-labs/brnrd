import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { environmentDisplay } from './railBench.ts';
import type { ConnectedRepo } from './repos.ts';

const source = readFileSync(new URL('./RailBench.svelte', import.meta.url), 'utf8');
const repo = { environment_default: 'host · default' } as ConnectedRepo;

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

test('the bench is mobile-first: bays stack before widening at md', () => {
	assert.match(source, /grid gap-6 md:grid-cols-2/);
	assert.doesNotMatch(source, /class="panel[^\"]*"/);
});

test('every bench pick and inherited rack control has a 44px floor', () => {
	assert.equal((source.match(/data-role="bench-pick"/g) ?? []).length, 3);
	assert.match(source, /\.pick-row\s*\{\s*min-height:\s*44px;?\s*\}/);
	assert.match(source, /button\[role='tab'\][\s\S]*min-height: 44px/);
	assert.match(source, /button\[data-role='rack-row-tap'\][\s\S]*min-height: 44px/);
});

test('workshop labels replace card titles with numbered bays', () => {
	assert.match(source, /<span>01<\/span> project/);
	assert.match(source, /<span>02<\/span> environment/);
	assert.match(source, /border-left: 3px solid/);
});
