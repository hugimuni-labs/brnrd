import assert from 'node:assert/strict';
import test from 'node:test';

import { environmentDisplay } from './railBench.ts';
import type { ConnectedRepo } from './repos.ts';

const repo = { environment_default: 'host · default' } as ConnectedRepo;

// #1516: the environment lane's own "default"/"default" collision — the
// badge sense (this is what the next wake resolves to) and the name sense
// (the environment is literally called `default`) must never collapse into
// one string a reader has nothing to cut on.
test('the resolved default renders its name and the default badge as two separate facts', () => {
	const display = environmentDisplay(repo, null);
	assert.equal(
		display.name,
		'host · default',
		'the name is never concatenated with the word "default"'
	);
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
