import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const template = readFileSync(new URL('../app.html', import.meta.url), 'utf8');

test('SvelteKit placeholders occur once and outside HTML comments', () => {
	const tokens = [...template.matchAll(/%sveltekit\.\w+%/gu)].map(([token]) => token);
	assert.deepEqual(tokens.sort(), ['%sveltekit.body%', '%sveltekit.head%']);

	for (const comment of template.matchAll(/<!--[\s\S]*?-->/gu)) {
		assert.doesNotMatch(comment[0], /%sveltekit\.\w+%/u);
	}
});
