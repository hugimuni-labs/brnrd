// The heddle rail's press semantics, extracted to plain TS (2026-08-11, his
// correction of the original all-minus-one reducer) so node's test runner
// reaches it — the logic used to live inline in +page.svelte's script,
// where no test could call it.
//
// All-lit (`selection === null`) is the default. Pressing a topic from
// there does not dim everything *except* that topic (the original,
// mistaken reading of "select all by default") — it makes the pressed
// topic the sole active filter. His words: "when I press one, it doesn't
// unselect it, but makes it the active filter." Once a filter is active,
// pressing toggles plain membership on the lit set: add an unlit topic,
// remove a lit one. Removing the last lit topic returns to all-lit rather
// than landing on an empty, nothing-shown filter — no press on this rail
// can reach a state that hides everything. Relighting every topic by hand
// collapses back to `null` for the same reason from the other side: an
// explicit full set and "all lit" render identically everywhere this
// selection is read (`HeddleRail`, `WarpGraphView`, `Cloth`), so keeping
// them as two distinct states would be a distinction with no visual
// difference.

/** One press on topic `id`, given the rail's current `selection` and the
 *  full set of canonical topic ids (`allIds`, for the relit-everything and
 *  empty-filter collapses). Pure — the caller persists the result. */
export function toggleHeddleSelection(
	selection: ReadonlySet<string> | null,
	id: string,
	allIds: readonly string[]
): Set<string> | null {
	if (selection === null) return new Set([id]);
	const next = new Set(selection);
	if (next.has(id)) next.delete(id);
	else next.add(id);
	if (next.size === 0 || next.size >= allIds.length) return null;
	return next;
}
