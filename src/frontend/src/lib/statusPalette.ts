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
export const STATUS_GOOD = '#e8b34a';
export const STATUS_WARN = '#7aa9c2';
export const STATUS_CRITICAL = '#9c8d7d'; // void ash — spent, desaturated warm-grey, not red
export const STATUS_UNKNOWN = '#57534e'; // stone-600 — recedes, not a fourth status hue
