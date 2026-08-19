// Row/chip state chrome shared by SpoolRack and ControlStrip's project /
// environment / runner-chip lists ("clear the ground under the rail",
// 2026-08-19). Four different amber pairs and one disabled recipe were
// hand-copied across the two components with drift nobody chose — this
// module names each state once so every site imports the same string
// instead of retyping it. Every value here is unchanged from what its site
// already rendered; see the commit message for which literal survived
// where duplicates existed and which states were kept distinct on purpose.

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
