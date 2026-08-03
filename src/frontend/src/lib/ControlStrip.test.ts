import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ControlStrip.svelte');
const generated = join(here, '.controlStrip.generated.mjs');

// The rail's two faces (his 08-02 steer: "the resource management should
// stay on top, maybe in a collapsed way"): the full strip when the reader
// is at the top of the page, and a one-line slim bar once they scroll —
// the page's `condensed` verdict, overridable in the client by pinning the
// rail back open ($effect state, so a server render always shows the slim
// bar when condensed). Same server-side render dance as WarpStack's tests:
// compile with stubbed children, assert on the produced markup.
async function renderStrip(props: {
	runners: null;
	shells: null;
	condensed?: boolean;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'ControlStrip'
	});
	const runnable = compiled.js.code
		.replace(/import\s+SpoolRack\s+from\s*'\.\/SpoolRack\.svelte';/, 'const SpoolRack = () => {};')
		// Node's ESM resolver needs the extension the compiler drops. This was
		// a hand-listed set — `./controlStrip`, `./transitions`, `./quota`,
		// `./tankForecast`, `./statusPalette` — and adding an import to the
		// component broke these tests with a module-not-found that reads
		// nothing like "you forgot to update a list in a test file". A class
		// defined by listing its members meets the member nobody listed; the
		// structural property is "a relative specifier with no extension", so
		// match that instead. The character class excludes `.`, so anything
		// already carrying one (`./SpoolRack.svelte`, handled above) is left
		// alone.
		.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('at rest the rail is the full strip, and the block is named "next pick" — never "dispatch"', async () => {
	const body = await renderStrip({ runners: null, shells: null });
	ok(body.includes('next pick'), 'the renamed block header renders');
	ok(!body.includes('dispatch'), 'the retired name is gone from the markup');
	ok(!body.includes('expand the rail'), 'no slim bar while uncondensed');
});

test('condensed, the rail is one slim line that expands on demand', async () => {
	const body = await renderStrip({ runners: null, shells: null, condensed: true });
	ok(body.includes('expand the rail'), 'the slim bar renders');
	ok(body.includes('rail'), 'named for what it is');
	ok(!body.includes('next pick'), 'the full strip stays folded');
});

// The whole surface opens the rack now (2026-08-03, the rack answers
// everywhere): the header that used to be only the left block is one
// pressable button wrapping the left block, the fuel grid, and the tank
// line — never expanded in this SSR harness (there is no interaction to
// drive `expanded` to true), so what these can pin is the resting/closed
// shape: one disclosure button, carrying the shared collapse chrome.
test("the full strip's header is one pressable disclosure over the whole surface", async () => {
	const body = await renderStrip({ runners: null, shells: null });
	ok(body.includes('panel--pressable'), 'the shared pressable chrome renders on the header');
	ok(body.includes('aria-label="expand the rack"'), 'closed, the header names what a tap does');
	// Exactly one `aria-expanded` in the full form belongs to the rack's own
	// disclosure — "Keep aria-expanded on exactly one element per disclosure".
	const expandedAttrs = body.match(/aria-expanded=/g) ?? [];
	ok(expandedAttrs.length === 1, `expected one aria-expanded, found ${expandedAttrs.length}`);
});

// "Once fully collapsed, render it like the current collapsed forms: greyed
// out a bit" — scoped to the forms that are already the scroll-away compact
// rendering (his 2026-08-03 steer: the ordinary resting/closed look of the
// full strip stays exactly as it was, so `panel--collapsed` belongs on the
// slim bar only, never on the full form's own header).
test('the slim bar is the scroll-away collapsed form and wears the collapsed chrome', async () => {
	const body = await renderStrip({ runners: null, shells: null, condensed: true });
	ok(body.includes('panel--collapsed'), 'the slim bar carries the desaturated collapsed variant');
});

test('the full strip\'s resting header carries no collapsed chrome — his "already liked" steer', async () => {
	const body = await renderStrip({ runners: null, shells: null });
	ok(!body.includes('panel--collapsed'), 'the everyday view renders exactly as it did before');
});
