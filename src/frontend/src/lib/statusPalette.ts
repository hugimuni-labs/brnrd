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
// CRITICAL = dying ember — reserved for an actual danger/exhaustion signal,
// not a routine "needs attention" state · UNKNOWN recedes rather than
// adding a fourth status hue. Never color alone: every caller pairs this
// with an icon/label, per the dataviz skill's own rule.
//
// Contrast validated against #0c0906 (body), ~#171009 (panel), #1c1917
// (stone-900 track) via dataviz's scripts/validate_palette.js — all ≥
// 3.7:1. Confirmed live with the maintainer (2026-07-08): "amber instead
// of green, frost/bluish instead of orange-warn... no red/green color
// language" — this is that instruction, not a new design call.
export const STATUS_GOOD = '#e8b34a';
export const STATUS_WARN = '#7aa9c2';
export const STATUS_CRITICAL = '#c0523f';
export const STATUS_UNKNOWN = '#57534e'; // stone-600 — recedes, not a fourth status hue
