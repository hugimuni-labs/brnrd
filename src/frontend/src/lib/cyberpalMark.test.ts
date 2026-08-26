import assert from 'node:assert/strict';
import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import { BRNRD_DEFAULTS, brnrdBody } from './brandGeometry.ts';
import type { BrnrdConstants, FaceName } from './brandGeometry.ts';
import {
	CYBERPAL_DEFAULTS,
	FACE_DEMO_FRAMES,
	buildCycle,
	faceForFrame,
	inkedBody,
	markBox
} from './cyberpalMark.ts';

// One test file for the pair (`cyberpalMark.ts` + `CyberpalMark.svelte`) on
// purpose: their natural test filenames differ only by case, and this repo
// gets developed on a case-insensitive filesystem where those are the same
// file — two names would silently clobber each other (measured here, once).

// ── frame → face ─────────────────────────────────────────────────────────
// The daemon's mood wire speaks the emote grammar; the drawn being has six
// faces. The mapping must be deterministic and total — an unreadable mood
// rests, it never guesses.

test('mood frames map onto drawn faces by eye family, kawaii only when earned', () => {
	const cases: Array<[string, FaceName]> = [
		['b^w^d', 'kawaii'], // lifted eyes + open mouth — the full kawaii
		['b^o^d', 'kawaii'],
		['b^_^d', 'up'], // lifted eyes, flat mouth — joy without the squeal
		['bo_od', 'wide'],
		['bO.Od', 'wide'],
		['b-_-d', 'flat'],
		['b-=-d', 'grip'], // level eyes, doubled mouth — the working grit
		['bx_xd', 'grip'],
		['b·_·d', 'rest']
	];
	for (const [frame, face] of cases) {
		assert.equal(faceForFrame(frame), face, `${frame} wears ${face}`);
	}
});

test('unparseable, empty, and absent frames all rest — the fallback impersonates nothing', () => {
	for (const frame of [null, undefined, '', 'b', 'not a face at all', 'b??!?d']) {
		assert.equal(faceForFrame(frame as string | null), 'rest');
	}
});

test('every bench demo frame round-trips to exactly the face it demos', () => {
	for (const [face, frame] of Object.entries(FACE_DEMO_FRAMES)) {
		assert.equal(faceForFrame(frame), face, `${frame} demos ${face}`);
	}
});

// ── the choreography ─────────────────────────────────────────────────────

test('every cycle blinks, speaks the name readably, and returns to its base', () => {
	for (const base of ['rest', 'kawaii', 'wide', 'grip'] as FaceName[]) {
		const cycle = buildCycle(base);
		// It ends where it began, so settling to `null` (the resting base) is
		// seamless — no frame jump at the cycle boundary.
		assert.equal(cycle[cycle.length - 1].frame, base);
		// The name appears, and its longest hold is glitch-free and long
		// enough to actually read: the name is the message, the glitch is
		// only the transition.
		const nameBeats = cycle.filter((b) => b.frame === 'name');
		assert.ok(nameBeats.length >= 1, `${base}: the being speaks its name`);
		const longest = nameBeats.reduce((a, b) => (b.hold > a.hold ? b : a));
		assert.ok(longest.hold >= 900, `${base}: the name holds long enough to read`);
		assert.equal(longest.glitch, false, `${base}: the readable name beat is clean`);
		// Some beat glitches — the transition is a glitch, per the brief.
		assert.ok(
			cycle.some((b) => b.glitch),
			`${base}: the transition glitches`
		);
		// Every hold is positive; a zero-hold beat would wedge the timer walk.
		for (const b of cycle) assert.ok(b.hold > 0);
	}
});

test('a flat-faced being blinks with a different face than its base', () => {
	// Blink = eyes shut = the `flat` face; a being already wearing `flat`
	// must not "blink" into itself and render a cycle with no visible beat.
	const cycle = buildCycle('flat');
	assert.notEqual(cycle[0].frame, 'flat');
});

// ── the box ──────────────────────────────────────────────────────────────

test('the crowned box contains the crownless one, and both hold every stroke', () => {
	const bare: BrnrdConstants = { ...BRNRD_DEFAULTS, CROWN: 'none' };
	const crowned = markBox(CYBERPAL_DEFAULTS);
	const plain = markBox(bare);
	assert.ok(crowned.x < plain.x, 'branch antennas widen the box');
	assert.ok(crowned.w > plain.w);
	// Strokes span x ∈ [leftStave-38, rightStave+38] with the branch crown and
	// round caps half a stroke past that; the box must cover it with margin.
	assert.ok(crowned.x <= 96 - 24 - 38 - CYBERPAL_DEFAULTS.STROKE / 2);
	assert.ok(crowned.y <= CYBERPAL_DEFAULTS.STAVE_TOP - CYBERPAL_DEFAULTS.STROKE / 2);
	assert.ok(crowned.y + crowned.h >= CYBERPAL_DEFAULTS.BASELINE + CYBERPAL_DEFAULTS.STROKE / 2);
});

// ── the ink ──────────────────────────────────────────────────────────────

test('inkedBody re-inks the molten eye fill and changes nothing else', () => {
	const raw = brnrdBody('rest', BRNRD_DEFAULTS);
	const inked = inkedBody('rest', BRNRD_DEFAULTS, 'currentColor');
	assert.ok(raw.includes('url(#molten)'), 'the raw body references the stone gradient');
	assert.ok(!inked.includes('url(#molten)'), 'the inked body references no missing def');
	assert.equal(inked, raw.replaceAll('url(#molten)', 'currentColor'));
	// The name frame has no dot fill at all — re-inking must be a no-op.
	assert.equal(inkedBody('name', BRNRD_DEFAULTS, 'red'), brnrdBody('name', BRNRD_DEFAULTS));
});

// ── the component, server-rendered ───────────────────────────────────────
// Same server-side render dance as WinkWordmark.test.ts: compile with
// `generate: 'server'`, restore the extensions the compiler strips off
// relative specifiers, assert on the produced markup. `onMount` never runs
// server-side, so what renders here is the *resting* state — which is
// exactly the contract worth pinning: at rest the being wears its base
// face, clean, with no aberration ghosts.

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'CyberpalMark.svelte');
const generated = join(here, '.cyberpalMark.generated.mjs');

async function renderMark(props: {
	label?: string;
	class?: string;
	frames?: string[] | null;
	pitch?: number | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'CyberpalMark' });
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

test('at rest the being is a drawn face: accessible name, dot eyes, no ghosts', async () => {
	const body = await renderMark({});
	ok(body.includes('aria-label="brnrd"'), 'the drawing carries the accessible name');
	ok(body.includes('<svg'), 'the mark is drawn, not typed');
	// The neutral rest face: filled dot eyes inked for inline use — no
	// reference to the stone register's #molten def, which does not exist
	// on a page.
	ok(body.includes('fill="currentColor"'), 'eye dots are inked with currentColor');
	ok(!body.includes('url(#molten)'), 'no dangling gradient reference');
	// No glitch at rest: the aberration ghosts only exist during glitch
	// beats, which only a running timer reaches.
	ok(!body.includes('#ff3b30'), 'no red ghost at rest');
	ok(!body.includes('#3ad8e6'), 'no cyan ghost at rest');
});

test('a joyful mood on the wire changes which face renders at rest', async () => {
	const rest = await renderMark({});
	const joy = await renderMark({ frames: ['b^w^d'] });
	ok(rest !== joy, 'the wire mood reaches the drawing');
	// The kawaii mouth is the dropped-n `lown` glyph — two verticals plus a
	// peak — where rest's mouth is a single flat bar; the cheapest stable
	// distinction is that the joy render draws more path segments.
	const paths = (s: string) => (s.match(/<path /g) ?? []).length;
	ok(paths(joy) > paths(rest), 'the kawaii face draws more strokes than rest');
});

test('the label prop is the aria-label; the sway class is present for CSS to animate', async () => {
	const body = await renderMark({ label: 'brnrd — home' });
	ok(body.includes('aria-label="brnrd — home"'));
	ok(body.includes('cyberpal-sway'), 'the sway rides a class so reduced-motion CSS can still it');
});
