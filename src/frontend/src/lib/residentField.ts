// The resident field — the live body's topology and its event grammar.
//
// `design-resident-field.md` + `research-resident-as-explorable-machine
// -2026-08-25.md` (home kb): the occupied Shed is not a peer-card grid, it
// is a body — the resident as the dominant node, dispatched strands as
// limbs connected through their real parent edges, one visible level, and
// **motion only as a receipt**: every pulse corresponds to one recorded
// event (a spawn, a boundary, a completion, an injection). No event ⇒ the
// field is still. This module owns the pure derivations — topology from a
// live-runs snapshot, and the event diff between two consecutive snapshots
// — so the Svelte component draws and never decides.

import type { LiveRun, LiveRunEdge } from './liveRuns';

/** Stable key for a run row — presence id falls back when run_id is absent
 *  (pre-upgrade daemon rows). Same fallback every consumer of this wire
 *  already uses. */
export function fieldRunKey(run: Pick<LiveRun, 'id' | 'run_id'>): string {
	return run.run_id || run.id;
}

export interface FieldLimb {
	run: LiveRun;
	/** Collapsed descendants beneath this limb (`+N hands`): the phone field
	 *  keeps one visible level (research §6); deeper strands stay fully
	 *  inspectable on the run route, never re-drawn here. */
	hands: number;
}

export interface FieldRoot {
	run: LiveRun;
	limbs: FieldLimb[];
	/** True when this root is itself a strand whose dispatcher is no longer
	 *  on the board — it renders as its own cell but keeps its strand
	 *  identity rather than being promoted to a fake resident. */
	orphan: boolean;
}

function startedAtMs(run: LiveRun): number {
	const t = run.started_at ? Date.parse(run.started_at) : NaN;
	return Number.isNaN(t) ? Infinity : t;
}

/**
 * Snapshot → topology. Roots are the runs that answer to nobody on the
 * board: the resident thought(s), plus any strand whose parent has already
 * left the snapshot (marked `orphan`). First-level strands attach to their
 * root as limbs; anything deeper collapses into its ancestor limb's `hands`
 * count. Ordering is `started_at` ascending at both levels — the body
 * reads oldest-first, the way the work actually grew.
 */
export function buildField(runs: LiveRun[] | null | undefined): FieldRoot[] {
	const rows = runs ?? [];
	const byKey = new Map<string, LiveRun>();
	for (const run of rows) byKey.set(fieldRunKey(run), run);

	const isRoot = (run: LiveRun): boolean =>
		!run.is_subspawn || !run.parent_run_id || !byKey.has(run.parent_run_id);

	/** Walk to this run's root ancestor and its first-level limb, if any. */
	function anchorOf(run: LiveRun): { root: LiveRun; limb: LiveRun | null } {
		let node = run;
		let limb: LiveRun | null = null;
		const seen = new Set<string>([fieldRunKey(node)]);
		while (!isRoot(node)) {
			const parent = byKey.get(node.parent_run_id as string) as LiveRun;
			const parentKey = fieldRunKey(parent);
			if (seen.has(parentKey)) break; // defensive: a cycle in bad data
			seen.add(parentKey);
			limb = node;
			node = parent;
		}
		return { root: node, limb: node === run ? null : limb };
	}

	const roots = new Map<string, FieldRoot>();
	for (const run of rows) {
		if (!isRoot(run)) continue;
		roots.set(fieldRunKey(run), {
			run,
			limbs: [],
			orphan: !!run.is_subspawn
		});
	}
	const limbIndex = new Map<string, FieldLimb>();
	for (const run of rows) {
		if (isRoot(run)) continue;
		const { root, limb } = anchorOf(run);
		const rootEntry = roots.get(fieldRunKey(root));
		if (!rootEntry) continue;
		if (limb && fieldRunKey(limb) === fieldRunKey(run)) {
			const entry: FieldLimb = { run, hands: 0 };
			limbIndex.set(fieldRunKey(run), entry);
			rootEntry.limbs.push(entry);
		} else if (limb) {
			// A deeper descendant: charge it to its first-level ancestor.
			const holder = limbIndex.get(fieldRunKey(limb));
			if (holder) holder.hands += 1;
		}
	}

	const out = [...roots.values()];
	out.sort((a, b) => startedAtMs(a.run) - startedAtMs(b.run));
	for (const root of out) root.limbs.sort((a, b) => startedAtMs(a.run) - startedAtMs(b.run));
	return out;
}

// ── the event grammar ───────────────────────────────────────────────────────

export type FieldEventKind =
	/** A new run appeared on the board — a limb assembles, the packet runs
	 *  outward from its dispatcher. */
	| 'spawn'
	/** A run left the board — its work returns to the dispatcher; the packet
	 *  runs inward. */
	| 'return'
	/** A run crossed a tool/lifecycle boundary (`edge.at` moved) — a local
	 *  tick at that node, never a travelling packet: the act is the node's
	 *  own. */
	| 'boundary'
	/** The boundary that folded the world in (`edge.injected`) — inbound
	 *  correspondence reached the runner; the packet arrives from the portal
	 *  edge of the field. */
	| 'inject'
	/** Correspondence arrived at the run's door (`portals.pending` rose) —
	 *  the message drops from the portal and rests, *put to read*, until a
	 *  boundary attests the read. */
	| 'message'
	/** The resting correspondence was folded in (`portals.pending` fell) —
	 *  the read is attested, the resting marker travels home instead of
	 *  silently vanishing (the maintainer's live read of the room,
	 *  2026-08-26: "the animation didn't happen really, just the message
	 *  diamonds disappeared from the portal"). */
	| 'read';

export interface FieldEvent {
	kind: FieldEventKind;
	/** The run the event belongs to. For `return` the run is no longer in
	 *  the snapshot; `parentId` names where the pulse lands. */
	runId: string;
	parentId: string | null;
}

/**
 * Two consecutive snapshots → the receipts that may move. A `null` previous
 * snapshot is the mount: nothing "happened", so nothing moves — the field
 * assembles via its own state-birth reveal and stands still until the world
 * produces an event.
 */
export function diffFieldEvents(
	prev: LiveRun[] | null | undefined,
	next: LiveRun[] | null | undefined
): FieldEvent[] {
	if (prev == null) return [];
	const before = new Map<string, LiveRun>();
	for (const run of prev) before.set(fieldRunKey(run), run);
	const after = new Map<string, LiveRun>();
	for (const run of next ?? []) after.set(fieldRunKey(run), run);

	const events: FieldEvent[] = [];
	for (const [key, run] of after) {
		const was = before.get(key);
		if (!was) {
			events.push({ kind: 'spawn', runId: key, parentId: run.parent_run_id ?? null });
			continue;
		}
		const edgeMoved = !!run.edge?.at && run.edge.at !== was.edge?.at;
		if (edgeMoved) {
			events.push({
				kind: run.edge?.injected ? 'inject' : 'boundary',
				runId: key,
				parentId: run.parent_run_id ?? null
			});
		}
		const pendingNow = run.portals?.pending ?? 0;
		const pendingWas = was.portals?.pending ?? 0;
		if (pendingNow > pendingWas) {
			events.push({ kind: 'message', runId: key, parentId: run.parent_run_id ?? null });
		} else if (pendingNow < pendingWas) {
			events.push({ kind: 'read', runId: key, parentId: run.parent_run_id ?? null });
		}
	}
	for (const [key, run] of before) {
		if (!after.has(key)) {
			events.push({ kind: 'return', runId: key, parentId: run.parent_run_id ?? null });
		}
	}
	return events;
}

// ── the act palette ─────────────────────────────────────────────────────────

// Boundary acts → phosphor colors, one per act class, mirroring the
// operator console's EDGE row classes (`operator_console/tui.py`, #1623) —
// hand-mirrored across the Python/TS seam the same way `RELIC_ICONS`
// mirrors `_TAIL_NOUNS`; if the console repaints an act, repaint it here in
// the same change. The web field and the console are two renderers of one
// boundary record and must not disagree about what `mutate` looks like.
export const ACT_COLORS: Record<string, string> = {
	orient: '#6ec6ff',
	probe: '#8fd6c4',
	mutate: '#d3a75e',
	publish: '#7fbf7f',
	dispatch: '#c792ea',
	wait: '#8e806c'
};
const ACT_UNKNOWN = '#a99f8c';

export function actColor(act: string | null | undefined): string {
	if (!act) return ACT_UNKNOWN;
	return ACT_COLORS[act] ?? ACT_UNKNOWN;
}

/** The edge line with the act split out so the act can wear its color:
 *  `⌁ mutate · git status --short · 412 B` renders act-colored, detail
 *  parchment. */
export function edgeParts(
	edge: LiveRunEdge | null | undefined
): { act: string | null; detail: string | null; color: string } | null {
	if (!edge) return null;
	if (!edge.act && !edge.detail) return null;
	return { act: edge.act, detail: edge.detail, color: actColor(edge.act) };
}
