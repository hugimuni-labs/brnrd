import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'HeddleStrip.svelte');
const generated = join(here, '.heddleStrip.generated.mjs');

async function renderStrip(weaving: ReadonlySet<string>): Promise<string> {
	const compiled = compile(readFileSync(componentPath, 'utf8'), {
		generate: 'server',
		runes: true,
		name: 'HeddleStrip'
	});
	writeFileSync(generated, compiled.js.code);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				threads: [
					{
						canonicalId: 'loom',
						title: 'The loom',
						face: { glyph: 'ᚠ', color: 'hsl(42 48% 64%)', hue: 42 }
					}
				],
				selected: null,
				weaving
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('a live topic gives its heddle a visible weaving bolt', async () => {
	const lit = await renderStrip(new Set(['loom']));
	const unlit = await renderStrip(new Set());

	ok(lit.includes('aria-label="weaving now"'), 'the live topic is visibly lit');
	ok(!unlit.includes('aria-label="weaving now"'), 'an unclaimed topic stays unlit');
});
