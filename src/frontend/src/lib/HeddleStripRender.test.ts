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

test('a live topic makes its existing heddle glow without changing the rune', async () => {
	const lit = await renderStrip(new Set(['loom']));
	const unlit = await renderStrip(new Set());

	ok(
		/class="[^"]*heddle-weaving/.test(lit),
		'the existing live-topic button carries the glow state'
	);
	ok(lit.includes('aria-label="weaving now"'), 'the existing button names the live state');
	ok(lit.includes('text-shadow:'), 'the topic-colored rune receives a soft luminance');
	ok(!lit.includes('↯'), 'the rune gains no bolt glyph');
	ok(!/class="[^"]*heddle-weaving/.test(unlit), 'an unclaimed topic does not glow');
	ok(!unlit.includes('aria-label="weaving now"'), 'an unclaimed topic has no live-state name');
});

// The strip's glow must not depend on the heddle being filtered in: a run can
// be weaving a topic the reader has filtered out, and that is exactly when the
// glow carries information the eye has nowhere else to get.
test('a weaving topic glows in the strip even when it is not filtered in', async () => {
	const compiled = compile(readFileSync(componentPath, 'utf8'), {
		generate: 'server',
		runes: true,
		name: 'HeddleStrip'
	});
	writeFileSync(generated, compiled.js.code);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}-off`);
		const html = render(module.default, {
			props: {
				threads: [
					{
						canonicalId: 'loom',
						title: 'The loom',
						face: { glyph: 'ᚠ', color: 'hsl(42 48% 64%)', hue: 42 }
					},
					{
						canonicalId: 'post',
						title: 'The post',
						face: { glyph: 'ᚱ', color: 'hsl(2 48% 64%)', hue: 2 }
					}
				],
				selected: new Set(['post']),
				weaving: new Set(['loom'])
			}
		}).body;
		ok(html.includes('text-shadow:'), 'the filtered-out weaving heddle still glows');
	} finally {
		rmSync(generated, { force: true });
	}
});
