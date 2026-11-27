import assert from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import type { ShownWish } from './wishLink.ts';

// The thread page's wish block, server-rendered. wishLink.test.ts covers the
// chain walk; this pins that a query's wish is the one opened, with the letter.
const here = dirname(fileURLToPath(import.meta.url));
const generated = join(here, '.wishOpen.generated.mjs');

async function draw(
	wishes: ShownWish[],
	selected: string | null,
	missing: string | null
): Promise<string> {
	const compiled = compile(readFileSync(join(here, 'WishOpen.svelte'), 'utf8'), {
		generate: 'server',
		runes: true,
		name: 'WishOpen'
	});
	writeFileSync(generated, compiled.js.code);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props: { wishes, selected, missing } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

const REQUEST = 'please trace this pull request back to the letter that asked';
const wishes: ShownWish[] = [
	{
		id: 'w-sign',
		line: 'the wish',
		state: 'heard',
		chain: [
			{ id: 'l-ada', kind: 'letter', from: 'person:ada', at: null, line: REQUEST, person: true }
		]
	},
	{ id: 'w-other', line: 'the other wish', chain: [] }
];

after(() => rmSync(generated, { force: true }));

test('the query wish opens with the requesting letter, and a stranger is one line', async () => {
	const html = await draw(wishes, 'w-sign', null);
	assert.ok(html.includes('data-testid="wish-selected"'));
	assert.ok(html.includes('data-wish="w-sign"'));
	assert.ok(html.includes(REQUEST));
	assert.ok(html.includes('data-request="true"'));
	const other = html.indexOf('data-wish="w-other"');
	assert.ok(other !== -1);
	assert.ok(!html.slice(other, other + 400).includes(REQUEST));

	const missing = await draw(wishes, null, 'w-nope');
	assert.ok(missing.includes('data-testid="wish-missing"'));
	assert.ok(missing.includes('no wish w-nope on this thread'));
	assert.ok(!missing.includes('data-testid="wish-selected"'));
	assert.ok(!missing.includes(REQUEST));
});
