import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { DELIVERY_TONE_CLASS, deliveryToneClass } from './deliveryTone.ts';

test('every delivery tone resolves, and an unknown one falls back rather than blanking', () => {
	for (const tone of ['delivered', 'collected', 'pending', 'undeliverable', 'unknown']) {
		assert.equal(deliveryToneClass(tone), DELIVERY_TONE_CLASS[tone]);
	}
	// The three ways a caller reaches this with nothing useful. None may
	// return `undefined` into a `class=` attribute.
	assert.equal(deliveryToneClass(null), DELIVERY_TONE_CLASS.unknown);
	assert.equal(deliveryToneClass(undefined), DELIVERY_TONE_CLASS.unknown);
	assert.equal(deliveryToneClass('a-status-nobody-has-written-yet'), DELIVERY_TONE_CLASS.unknown);
});

test('no delivery tone is green', () => {
	for (const [tone, cls] of Object.entries(DELIVERY_TONE_CLASS)) {
		assert.ok(
			!/emerald|green|lime|teal/.test(cls),
			`${tone} is painted ${cls}; statusPalette.ts excludes the green family by name`
		);
	}
});

// The guard, not the observation. `statusPalette.ts` has said "red is
// reserved for a broken contract" and "a direct frost→amber lerp crosses an
// unintended green" for months, in a comment — and four components carried
// `text-emerald-400` anyway, one of them on the dashboard home. A palette
// rule that only a reader can enforce is a rule that drifts every time
// somebody reaches for the traffic-light reflex.
//
// This is a lint, deliberately: the property being asserted *is* a property
// of the source text, not of any behaviour, so reading the source is the
// honest way to check it rather than a shortcut around driving something.
const FORBIDDEN_HUE =
	/\b(?:text|bg|border|border-l|border-t|border-r|border-b|from|via|to|ring|shadow|decoration|outline|fill|stroke|accent|caret|divide)-(?:emerald|green|lime|teal)-\d{2,3}\b/;

function* walk(dir: string): Generator<string> {
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry.startsWith('.')) continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) yield* walk(path);
		else if (path.endsWith('.svelte')) yield path;
	}
}

test('no .svelte file paints with a hue the palette excludes', () => {
	const offenders: string[] = [];
	for (const file of walk('src')) {
		readFileSync(file, 'utf8')
			.split('\n')
			.forEach((line, index) => {
				const hit = line.match(FORBIDDEN_HUE);
				if (hit) offenders.push(`${file}:${index + 1} — ${hit[0]}`);
			});
	}
	assert.deepEqual(
		offenders,
		[],
		`green is not in this palette (statusPalette.ts, layout.css):\n${offenders.join('\n')}`
	);
});
