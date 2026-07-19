/**
 * Lenses over the loom's past shelf (wyrd §4 band 2).
 *
 * This is the surface that replaces `.task-classification`, and the shape of
 * the replacement is the whole point. That column asked each run to *coin* a
 * one-slug label for itself at closeout; measured 2026-07-19 it had produced
 * 165 distinct slugs across 270 rows, 141 of them appearing exactly once. Its
 * median was a population of one.
 *
 * The maintainer's call, same day: *"we don't need deterministic pattern
 * matching most of the time, rather an introspection means for an intelligent,
 * unique each time weave."* That rules out the obvious replacement as well as
 * the original — a fixed `source_system × produce shape` enum is the same
 * classifier one layer down, with the coining moved from the resident to the
 * product. It would be wrong about the shape-space within a release.
 *
 * So a lens is **derived, never declared**:
 *
 * - Origin lenses are read off the `source_system` values actually present in
 *   the rows on screen. The daemon writes that field, a run cannot improvise
 *   it, and nothing here holds a list of the legal values — a new dispatch
 *   source appears in this band the day it appears in the data, with no
 *   frontend change and no vocabulary to update.
 * - Shape lenses are read off the relic manifest, which is a record of what a
 *   run *produced*. A run that opened a PR shipped; a run with no produce at
 *   all is bare. That cannot drift from the truth because it **is** the truth
 *   — the same `relics.collect` output the node and the ledger already read.
 * - The stack lens is `is_subspawn`, already exact.
 *
 * The cost, stated honestly: a derived shape loses *intent*. A run that meant
 * to ship and got blocked produces nothing and lenses as `bare`. That is the
 * better ledger — it records what happened rather than what was hoped — and
 * intent already has two homes in the run's `.name` and its `summary` relic.
 *
 * Empty lenses never render. A vocabulary that shows you options matching
 * nothing is a vocabulary asserting a shape-space it hasn't checked.
 */

import type { RelicRecord, RunLedgerRow } from './runLedger';

/** Which facet a lens slices on — the grouping the chip row renders under. */
export type LensFacet = 'all' | 'origin' | 'shape' | 'stack' | 'artifact';

export interface Lens {
	/** Stable key for selection state and tests. */
	id: string;
	/** What the chip says. Lowercase; the band is a monospace instrument. */
	label: string;
	facet: LensFacet;
	/** Rows this lens matches, in the current window. */
	count: number;
}

/** The default lens: everything in the window, no slice. */
export const LENS_ALL = 'all';

/**
 * The artifact lens (wyrd §4 band 2, "a lens over artifact edges with
 * `needs-review` state"). Alone among the lenses it does not filter runs — an
 * open PR outlives the run that opened it, so its edge is to the artifact, not
 * to a row on the shelf. It is a lens rather than the standing §2d section it
 * replaces because "what is waiting on me" is a question you *ask* of the
 * board, not a panel that should occupy it while the answer is nothing.
 */
export const LENS_REVIEW = 'review';

function isKb(relic: RelicRecord): boolean {
	return relic.kind === 'kb' || relic.kind === 'kb_page';
}

/**
 * The produce shape of one run, read off its relics.
 *
 * Ordered by strength of receipt, and the order is the claim: a PR is a
 * stronger statement about a run than the commits under it, and commits are a
 * stronger statement than a page. A run is named for the furthest it got.
 */
export type ProduceShape = 'shipped' | 'committed' | 'filed' | 'bare';

export function produceShape(relics: RelicRecord[]): ProduceShape {
	if (relics.some((relic) => relic.kind === 'pr')) return 'shipped';
	if (relics.some((relic) => relic.kind === 'commit')) return 'committed';
	if (relics.some(isKb)) return 'filed';
	return 'bare';
}

const SHAPE_LABELS: Record<ProduceShape, string> = {
	shipped: 'shipped',
	committed: 'committed',
	filed: 'filed',
	bare: 'bare'
};

/** Origin label: the daemon's own `source_system`, normalised for the chip. */
function originLabel(source: string): string {
	return source.toLowerCase().replaceAll('_', ' ');
}

/**
 * Does this row pass this lens?
 *
 * Exported because it is the whole contract between the chip row and the
 * shelf: the count on a chip and the rows behind it must be computed by one
 * function or they will disagree the first time either is edited.
 */
export function lensMatches(row: RunLedgerRow, lensId: string): boolean {
	if (lensId === LENS_ALL) return true;
	// The artifact lens does not narrow the shelf. An open PR outlives the run
	// that opened it — often by days — so filtering the past shelf down to
	// "runs that produced a PR still awaiting review" would answer a question
	// nobody asked and hide the rest of the board while doing it. It changes
	// what the *detail frame* renders instead; the spine stays whole.
	if (lensId === LENS_REVIEW) return true;
	if (lensId === 'stack:worker') return row.is_subspawn === true;
	if (lensId.startsWith('origin:')) {
		return (row.source_system ?? '') === lensId.slice('origin:'.length);
	}
	if (lensId.startsWith('shape:')) {
		return produceShape(row.external_refs ?? []) === lensId.slice('shape:'.length);
	}
	// An unknown lens matches nothing rather than everything. A stale selection
	// (a lens that was on screen and whose rows have since aged out of the
	// window) must read as an empty slice, not silently as "all" — the latter
	// shows a full shelf under a chip claiming to narrow it.
	return false;
}

export function applyLens(rows: RunLedgerRow[], lensId: string): RunLedgerRow[] {
	if (lensId === LENS_ALL) return rows;
	return rows.filter((row) => lensMatches(row, lensId));
}

/**
 * The lenses worth offering for *these* rows, with their counts.
 *
 * Nothing here is a fixed list except the ordering of the facets themselves.
 * Origins come from the data; shapes come from the manifests; a lens matching
 * zero rows is not offered at all.
 *
 * `reviewCount` is passed in rather than derived because the review lens
 * counts open PRs, which live in a different feed from the run ledger — the
 * one lens whose subject is an artifact rather than a run.
 */
export function availableLenses(rows: RunLedgerRow[], reviewCount = 0): Lens[] {
	const lenses: Lens[] = [{ id: LENS_ALL, label: 'all', facet: 'all', count: rows.length }];

	const origins = new Map<string, number>();
	const shapes = new Map<ProduceShape, number>();
	let workers = 0;
	for (const row of rows) {
		const source = (row.source_system ?? '').trim();
		if (source) origins.set(source, (origins.get(source) ?? 0) + 1);
		const shape = produceShape(row.external_refs ?? []);
		shapes.set(shape, (shapes.get(shape) ?? 0) + 1);
		if (row.is_subspawn === true) workers += 1;
	}

	// Origins sorted by weight, not alphabetically: the busiest dispatch source
	// is the one a reader reaches for, and it should not move as the tail
	// reshuffles. Ties break on the name so the order is stable across polls.
	for (const [source, count] of [...origins.entries()].sort(
		(a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
	)) {
		lenses.push({ id: `origin:${source}`, label: originLabel(source), facet: 'origin', count });
	}

	// Shapes in receipt order, so the row reads as a gradient of how far runs
	// got rather than as an arbitrary set.
	for (const shape of ['shipped', 'committed', 'filed', 'bare'] as const) {
		const count = shapes.get(shape) ?? 0;
		if (count > 0) {
			lenses.push({ id: `shape:${shape}`, label: SHAPE_LABELS[shape], facet: 'shape', count });
		}
	}

	if (workers > 0) {
		lenses.push({ id: 'stack:worker', label: '↳ workers', facet: 'stack', count: workers });
	}

	if (reviewCount > 0) {
		lenses.push({
			id: LENS_REVIEW,
			label: 'needs review',
			facet: 'artifact',
			count: reviewCount
		});
	}

	return lenses;
}

/**
 * Keep a selection honest across a poll or a window change.
 *
 * The lens vocabulary is derived, so it *moves*: step the past window from 6h
 * to 7d and a `origin:github` chip can appear; step back and it vanishes while
 * still selected. A selection pointing at a lens that is no longer offered
 * falls back to `all` rather than leaving the shelf empty under a chip the
 * reader can no longer see to un-click.
 */
export function reconcileLens(selected: string, lenses: Lens[]): string {
	return lenses.some((lens) => lens.id === selected) ? selected : LENS_ALL;
}
