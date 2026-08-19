// Row/chip state chrome shared by SpoolRack and ControlStrip's project /
// environment / runner-chip lists ("clear the ground under the rail",
// 2026-08-19). Four different amber pairs, one disabled recipe and the idle
// row were hand-copied across the two components with drift nobody chose —
// this module names each state once so every site imports the same string
// instead of retyping it. Every value here is unchanged from what its site
// already rendered; see the commit message for which literal survived where
// duplicates existed and which states were kept distinct on purpose.
//
// THIS IS NOT THE WHOLE VOCABULARY, and reading it as closed is the way it
// goes wrong. Audited across the frontend at extraction time: these two
// components alone carry **seven** distinct amber recipes, of which five are
// named here. The three that are not, all inside these same two files:
//
//   `border-amber-700/70 bg-amber-950/40`  SpoolRack.svelte:294  pin badge
//                                          when a next-wake request exists
//   `border-amber-600/80 bg-amber-950/60`  SpoolRack.svelte:253, :285
//                                          the "riding <thread>" sticky chip
//                                          and the "next wake" chip
//   `border-amber-800/60 bg-amber-950/40`  ControlStrip.svelte:268
//                                          the "pinned open" chip
//
// Each is one step off a constant below on one axis or both, which is
// exactly how a token set drifts back into hand-copied literals. They are
// chips rather than rows, and whether a chip is a fifth state or a rendering
// of an existing one is a design call — it belongs to w-68's bench, not to a
// mechanical extraction. App-wide the count is worse (nine distinct
// "selected" amber recipes, and the primary CTA is pixel-identical to one of
// them); that inventory is on w-68.

/** SpoolRack: a one-shot "next wake" request parked on this row — the
 *  brightest of the four, since it is the most time-boxed of the states
 *  ("next wake · requested" is not the same claim as the standing pin). */
export const SELECTED_REQUESTED = 'border-amber-600/80 bg-amber-950/40';

/** SpoolRack: the standing default/pin. Deliberately more muted than a
 *  one-shot request — see `SpoolRack.svelte`'s own comment on why the two
 *  must never look the same. */
export const SELECTED_PINNED = 'border-amber-800/70 bg-amber-950/20';

/** ControlStrip: the currently chosen option inside a picker list (repo,
 *  environment). */
export const SELECTED_OPTION = 'border-amber-700/70 bg-amber-950/30';

/** ControlStrip: the runner chip summarizing what is active right now.
 *  Same border as `SELECTED_OPTION`; a more opaque fill because it stands
 *  alone rather than inside a list of alternatives. */
export const SELECTED_ACTIVE = 'border-amber-700/70 bg-amber-950/55';

/** Shared "not tappable / not selectable" recipe: SpoolRack's dead rows,
 *  ControlStrip's non-dispatchable repos and unavailable environments. */
export const DISABLED_ROW = 'cursor-not-allowed border-stone-900/60 bg-stone-950/30 opacity-45';

/** The unavailable-row mark, prefixed onto a label when a row cannot be
 *  acted on. */
export const UNAVAILABLE_MARK = '✗ ';

/** The idle, tappable row — the direct sibling of every state above, and
 *  the branch each of their ternaries falls through to. Named for the same
 *  reason they are: it was hand-copied four times across the two files
 *  (`SpoolRack.svelte:153`, `ControlStrip.svelte:536`, `:579`, `:601`),
 *  which was the largest surviving duplication once the four amber pairs
 *  were done. */
export const IDLE_ROW = 'border-stone-800/60 bg-stone-900/30 hover:border-stone-600/70';
