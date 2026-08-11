// The warp item disclosure set (WarpGraphView.svelte, 2026-08-11 round: "the
// blocked items have references of the warp items that you can press… we
// should also open the clicked warp item while keeping the previous one
// open — the only exception to one-open; as soon as we press at any other
// place, everything collapses"). Plain TS, no Svelte import, so node's test
// runner reaches it directly (`warpGraphOpen.test.ts`) without a bundler —
// the same discipline `collapse.ts` and `warpGraph.ts` already keep for
// their own pure logic.
//
// One rule, two actions: a direct click on an item's own header always
// collapses to *that* item alone (today's toggle feel, preserved — click
// twice on the sole open item and it closes); following an in-graph
// `held by` / `unblocks` link is the one place two items are open at once,
// additive over whatever was already open. Nothing else grows the set —
// the reducer has no third action for a reason.

export type OpenAction =
	/** Direct click on an item's own disclosure header. */
	| { readonly type: 'toggle'; readonly id: string }
	/** Followed a `held by` / `unblocks` in-graph link — the target opens,
	 *  the source (and anything else already open) stays open. */
	| { readonly type: 'follow'; readonly id: string };

export function openReducer(current: ReadonlySet<string>, action: OpenAction): ReadonlySet<string> {
	if (action.type === 'follow') {
		if (current.has(action.id)) return current;
		return new Set([...current, action.id]);
	}
	// toggle: collapse to this item alone — unless it was already the *only*
	// open item, in which case the second press closes it (the toggle feel
	// this reducer preserves from the single-`openId` shape it replaces).
	if (current.size === 1 && current.has(action.id)) return new Set();
	return new Set([action.id]);
}
