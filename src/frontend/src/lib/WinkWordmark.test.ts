import { equal, ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

// Same server-side render dance as RunBlock.test.ts: compile with
// `generate: 'server'`, drop the extension the compiler strips off relative
// specifiers so Node's ESM resolver can find them, and assert on the
// produced markup.
const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'WinkWordmark.svelte');
const generated = join(here, '.winkWordmark.generated.mjs');

async function renderWink(props: {
	text?: string;
	class?: string;
	frames?: string[] | null;
	pitch?: number | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'WinkWordmark' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

// #1125's mobile report: a container sized by whichever frame currently
// rendered reflows the page every time the wink swaps to a frame of a
// different *actual* width — true of `bRnЯd`'s Cyrillic glyph in at least one
// mobile monospace fallback, even though every built-in frame is five
// characters. The fix stops sizing the box by content at all: every
// candidate frame renders, stacked in one grid cell, so the box is the
// browser's own measurement of the widest one rather than an assumption about
// glyph metrics.

test('every built-in wink frame is stacked in the markup, not swapped for a single text node', async () => {
	const body = await renderWink({});
	// The resting text plus all seven built-in frames — case-insensitive glyphs
	// stacked once each, not re-rendered per frame index. `<` is escaped by
	// the SSR text interpolation, so the wink-eyed frame is checked in its
	// rendered (`&lt;`) form rather than its source literal.
	for (const glyph of ['brnrd', 'bRnЯd', 'b-n-d', 'b^n-d', 'b^n^d', 'b^n&lt;d']) {
		ok(body.includes(glyph), `frame "${glyph}" is present in the stacked markup`);
	}
	// Every stacked candidate shares the same grid cell, so the box's width is
	// whichever one the browser measures widest — never a per-frame resize.
	const cellCount = (body.match(/\[grid-area:1\/1\]/g) ?? []).length;
	// text (1) + FRAMES (7, one repeated glyph still gets its own cell)
	equal(cellCount, 8);
});

test('at rest (server render, no onMount) only the plain wordmark is visible; every frame is hidden', async () => {
	const body = await renderWink({ text: 'brnrd' });
	// SSR never runs the animation loop, so `frame` stays null: the resting
	// text renders without `visibility: hidden`, and all seven wink frames do.
	const hiddenCount = (body.match(/visibility: hidden/g) ?? []).length;
	equal(hiddenCount, 7);
});

test('wire frames replace the built-in wink entirely — the stacked set is exactly what was given', async () => {
	const body = await renderWink({ frames: ['b·_·d', 'b^_^d', 'b·_·d'] });
	ok(body.includes('b·_·d'), 'a wire frame renders');
	ok(body.includes('b^_^d'), 'a wire frame renders');
	ok(!body.includes('bRnЯd'), 'the built-in wink does not also render once wire frames arrive');
	// text (1) + 3 wire frames = 4 stacked cells, regardless of how many
	// characters any individual frame carries — a wider future emote costs
	// nothing here.
	const cellCount = (body.match(/\[grid-area:1\/1\]/g) ?? []).length;
	equal(cellCount, 4);
});

test('the outer element still carries a plain aria-label — the wink is presentational only', async () => {
	const body = await renderWink({ text: 'brnrd' });
	ok(body.includes('aria-label="brnrd"'), 'the static name is what a screen reader gets');
	// Every stacked frame is hidden from assistive tech; only the label speaks.
	const hiddenFrameCount = (body.match(/aria-hidden="true"/g) ?? []).length;
	equal(hiddenFrameCount, 8);
});
