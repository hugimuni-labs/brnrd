// Single source for the dashboard's status-color language — brnrd's own
// fixed scale (2026-07-08 pass, `WindowTrack.svelte`), not the dataviz
// skill's generic reference defaults these hexes used to be lifted from
// (#0ca30c/#fab219/#d03b3b — stock red/green/amber traffic-light, never
// actually reskinned). Extracted here after `LiveRuns`/`PRReviewQueue` were
// found still hard-coding the old stock hexes *underneath a comment
// claiming parity with WindowTrack's palette* — a real drift, not just a
// missed reskin: the comment asserted a fact the code didn't back up.
// One module closes that class of gap structurally (import, don't retype).
//
// Semantics, not domain names, because "good/warn/danger" means the same
// thing whether the domain is quota headroom, run heartbeat freshness, or
// PR review state: GOOD = hearth-lit amber (full warmth, the default alive
// state) · WARN = frost creeping in (cooling, leaving the firelight) ·
// CRITICAL = void ash — the fire spent, not the fire turned red-hot; reserved
// for an actual danger/exhaustion signal, not a routine "needs attention"
// state · UNKNOWN recedes rather than adding a fourth status hue. Never
// color alone: every caller pairs this with an icon/label, per the dataviz
// skill's own rule.
//
// CRITICAL was `#c0523f` ("dying ember") until 2026-07-08 evening — live-
// caught by the maintainer as "the 0% 5h quota line is still red" after the
// same-day palette pass had already banned red/green status language
// elsewhere. Root cause, measured not eyeballed: `#c0523f` sits at OKLCH-ish
// hue≈9°/sat≈51% — a genuinely red hue wearing an "ember" label, the same
// red/orange family the maintainer had explicitly ruled out, just dimmer.
// The fix isn't a darker red, it's a different family: three real peer
// registers (amber = alive, frost = cooling, void = spent) instead of
// amber-primary-with-two-narrow-accents. True near-black text still fails
// the contrast floor here (see the superseded comment this replaces), so
// "void" as a *foreground* hue means desaturated ash — warmth gone to grey,
// not warmth gone red — while the void body/panel canvas still does the
// darkness half of the work as background.
//
// Contrast validated against #0c0906 (body), ~#171009 (panel), #1c1917
// (stone-900 track) via dataviz's scripts/validate_palette.js — all ≥
// 3.7:1 (ash: 6.17 / 5.85 / 5.43; hue≈31°, sat≈14% — no red/orange cast).
// "Accretion disk in negative" — decided 2026-07-08 evening, scope 1 of the
// three named options (kb/design-brand-visual-language.md §"Frost
// brightened; accretion disk in negative floated"): the void/critical status
// accent only, not the `.panel` bracket-corner chrome (explicitly rejected —
// "the amber glow sharp corners we have gives it the right loki-vs-severance
// feel") and not a full Layer-3 hero visual yet. The motif itself: a dark
// void fill with light concentrated sharply at the boundary — an eclipse
// silhouette / event-horizon rim, inverted from a normal accretion disk's
// bright-center reading. Applied only to the `critical` level, since that's
// the one status meaning "spent/exhausted" the void register was named for;
// `ample`/`warn`/`unknown` keep the plain solid-fill treatment they've always
// had. A shared helper, not inlined per-caller CSS, because this codebase has
// already paid once (`LiveRuns`/`PRReviewQueue` drift, 2026-07-08 morning) for
// letting near-identical status styling diverge across files.
export type StatusLevel = 'ample' | 'low' | 'critical' | 'unknown' | string;

/** Style for the small round status dot. `critical` gets the void-disk
 * treatment (dark core, glowing rim); everything else keeps the plain
 * solid-fill dot with its soft halo. */
export function statusDotStyle(level: StatusLevel, color: string): string {
	if (level === 'critical') {
		return `background-color: #0c0906; border: 1px solid ${color}; box-shadow: 0 0 5px 1.5px ${color}, inset 0 0 2px ${color}80;`;
	}
	return `background-color: ${color}; box-shadow: 0 0 4px 1px ${color}90;`;
}

/** Blend a hex color toward white by `ratio` (0 = unchanged, 1 = pure
 * white). Used so the bar's outer glow can read brighter/whiter than the
 * fill itself (maintainer ask, 2026-07-15: "the glow looks right now, could
 * you please make it brighter/whiter?") without touching the fill color —
 * the fill still carries the level's own hue (amber/frost/ash), so low
 * quota still reads as concerning; only the halo around it whitens. */
function glowTint(hex: string, ratio: number): string {
	const n = parseInt(hex.slice(1), 16);
	const r = (n >> 16) & 0xff;
	const g = (n >> 8) & 0xff;
	const b = n & 0xff;
	const mix = (c: number) => Math.round(c + (255 - c) * ratio);
	return `#${[mix(r), mix(g), mix(b)].map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

/** Style for the draining quota/credits track's fill. `critical` reads as a
 * dark void body with a bright rim right at the fill's leading edge — the
 * same disk-in-negative grammar as the dot, stretched along a bar instead of
 * a circle — rather than a flat critical-colored block.
 *
 * The outer-glow halo uses `glowTint`, not the raw level color: whitening
 * just the glow (not the fill, not the empty track) reads as "brighter" per
 * the maintainer's ask while keeping the dark-empty-track / colored-fill
 * semantics — and the critical void's ash rim — intact. */
export function statusBarStyle(level: StatusLevel, color: string): string {
	const glow = glowTint(color, 0.55);
	if (level === 'critical') {
		return `background: linear-gradient(to right, #0c0906 0%, #0c0906 82%, ${color} 100%); box-shadow: 0 0 11px 1px ${glow}cc, inset 0 0 3px 0 rgba(0, 0, 0, 0.6);`;
	}
	return `background-color: ${color}; box-shadow: 0 0 9px 1px ${glow}cc, inset 0 0 3px 0 rgba(255, 255, 255, 0.25);`;
}

export const STATUS_GOOD = '#e8b34a';
// WARN lightened 2026-07-08 evening, direct ask ("frost could be a bit more
// white, crisp"): #7aa9c2 -> #a8cbdb, hue held near-identical (200.8° ->
// 198.8°, still not sky-300's 199.4° collision partner) while OKLCH-ish
// lightness moved 62% -> 76%, sat 37% -> 42% — cooler/brighter reads as
// "crisp" without chasing sky-300's near-saturated 95%, which is the axis
// that actually keeps the two from reading as one hue in WindowTrack's
// same-card "stale report" badge (comment below on STATUS_WARN's old value
// still applies: desaturation is the separator, not hue). Contrast still
// comfortably clears floor: 11.57/10.97/10.19 vs body/panel/track (dataviz
// validate_palette.js math, not eyeballed).
export const STATUS_WARN = '#a8cbdb';
export const STATUS_CRITICAL = '#9c8d7d'; // void ash — spent, desaturated warm-grey, not red
export const STATUS_UNKNOWN = '#57534e'; // stone-600 — recedes, not a fourth status hue
