/**
 * The contrast floor, as a test rather than as a rule nobody re-checks.
 *
 * 2026-07-19: reported as "really hard to read on a sunny day". Measured, it
 * was not a daylight problem — 160 pieces of text across 18 components sat
 * below WCAG AA against the canvas, most of it 9-11px mono, and it had failed
 * in a dark room the whole time. A dark-adapted eye reads sub-AA grey as
 * "quiet" rather than as missing; daylight raises the screen's effective black
 * floor and compresses exactly that end of the range, so the unreadable tier
 * is simply the first thing to vanish.
 *
 * It drifted in one utility class at a time and nothing was watching, which is
 * the part worth fixing permanently. These tests are that watch: the ramp's
 * declared values must clear AA, and components must reach for the semantic
 * tiers rather than re-introducing the raw dim stone ones.
 */
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const CANVAS = '#0c0906'; // body background — the void everything is read against
const PANEL = '#150f0a'; // .panel fill, very slightly lighter; the stricter of the two is what matters
const AA_SMALL = 4.5; // WCAG 1.4.3 for normal-size text, which is nearly all of this dashboard

function channel(value: number): number {
	const c = value / 255;
	return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** Relative luminance, WCAG 2.x definition. */
export function luminance(hex: string): number {
	const h = hex.replace('#', '');
	const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(h.slice(i, i + 2), 16)));
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
	const [x, y] = [luminance(a), luminance(b)];
	return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const cssPath = fileURLToPath(new URL('../routes/layout.css', import.meta.url));
const css = readFileSync(cssPath, 'utf8');

function declaredColor(name: string): string {
	const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
	assert.ok(match, `layout.css declares --${name}`);
	return match![1];
}

test('contrastRatio matches the WCAG reference points', () => {
	// Identical colours have no contrast; black on white is the definitional max.
	assert.equal(Math.round(contrastRatio('#ffffff', '#ffffff') * 100) / 100, 1);
	assert.equal(Math.round(contrastRatio('#000000', '#ffffff') * 100) / 100, 21);
	// The ratio is symmetric — order of arguments must not change the verdict.
	assert.equal(contrastRatio(CANVAS, '#f3e8d8'), contrastRatio('#f3e8d8', CANVAS));
});

test('every ink tier clears AA on both the canvas and a panel', () => {
	for (const tier of ['color-ink-quiet', 'color-ink-mute']) {
		const value = declaredColor(tier);
		for (const [surface, label] of [
			[CANVAS, 'canvas'],
			[PANEL, 'panel']
		] as const) {
			const ratio = contrastRatio(value, surface);
			assert.ok(
				ratio >= AA_SMALL,
				`--${tier} (${value}) is ${ratio.toFixed(2)}:1 on the ${label}, below AA's ${AA_SMALL}`
			);
		}
	}
});

test('the ink tiers stay distinguishable from each other', () => {
	// A ramp whose steps the eye cannot resolve is not a hierarchy — it is the
	// defect this replaced, where stone-600 (2.60:1) and stone-700 (1.93:1)
	// encoded a distinction nobody could ever read. Two tiers that collapse
	// toward each other would rebuild that failure at a legible brightness.
	const quiet = luminance(declaredColor('color-ink-quiet'));
	const mute = luminance(declaredColor('color-ink-mute'));
	assert.ok(quiet > mute, 'quiet must sit above mute');
	assert.ok(quiet / mute >= 1.2, 'the ramp step is too small to read as hierarchy');
});

test('the eyebrow annotation colours clear AA too', () => {
	// `.eyebrow` and its `// ` prefix are literal hex rather than ramp tiers
	// (they carry a warm tint that marks them as annotation). The prefix was the
	// least readable glyph on the surface at 2.49:1, so it is pinned here.
	const eyebrow = css.slice(css.indexOf('.eyebrow {'));
	const colors = [...eyebrow.matchAll(/color:\s*(#[0-9a-fA-F]{6})/g)].map((m) => m[1]);
	assert.ok(colors.length >= 2, 'both the eyebrow label and its prefix declare a colour');
	for (const color of colors.slice(0, 2)) {
		assert.ok(
			contrastRatio(color, CANVAS) >= AA_SMALL,
			`eyebrow colour ${color} is ${contrastRatio(color, CANVAS).toFixed(2)}:1, below AA`
		);
	}
});

test('components use the ink ramp, not the sub-AA stone tiers', () => {
	// The forcing function. Every one of these 160 uses arrived as a reasonable
	// local choice; the damage was only visible in aggregate, so the check has
	// to live where a single new one trips it.
	const roots = [
		fileURLToPath(new URL('.', import.meta.url)),
		fileURLToPath(new URL('../routes/', import.meta.url))
	];
	const offenders: string[] = [];

	const walk = (dir: string) => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			if (entry.name.startsWith('.')) continue;
			const path = `${dir}${entry.name}`;
			if (entry.isDirectory()) walk(`${path}/`);
			else if (/\.(svelte|ts)$/.test(entry.name) && !entry.name.endsWith('.test.ts')) {
				const source = readFileSync(path, 'utf8');
				for (const match of source.matchAll(/text-stone-([567]00)/g)) {
					offenders.push(`${entry.name}: text-stone-${match[1]}`);
				}
			}
		}
	};
	roots.forEach(walk);

	assert.deepEqual(
		offenders,
		[],
		`use text-ink-quiet / text-ink-mute instead:\n${offenders.join('\n')}`
	);
});
