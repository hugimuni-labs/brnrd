// The RoomGraph — the flattened operational-topology model
// (design-room-operational-topology.md, 2026-08-26). One semantic scene,
// compiled from wires that already exist (`fetchLiveRuns`, `fetchRunLedger`),
// consumed by any renderer — the `/ascii` reference camera first, the
// isometric room later. This module is a *projection*, never a source of
// truth: every field traces to an attested wire fact, and absence stays
// absence (the renderer draws nothing rather than a guess).
//
// The doc's three coordinate systems, mapped to what the wire can attest
// today:
//
//   WORLD    → islands (one per repo_label) + camps (branch/worktree)
//   PROCESS  → each actor's resolved Place: resource anchor + operation,
//              never `act → pixel` (resolvePlace below)
//   TIME     → live actors vs Cloth rows; the injected flag on the edge
//
// What the wire cannot attest yet is deliberately NOT modelled — the gaps
// stay visible in the renderer instead of being papered over here:
//   - per-event letters (wire carries `portals.pending` + oldest only) —
//     doc §Gaps #2; the rack renders a count, not fabricated envelopes;
//   - live spend (ledger rows carry tokens/usd; live runs carry none) —
//     doc §Gaps #4 is half-stale: Cloth spend IS served, live spend isn't;
//   - promises/owed (nothing on either dashboard wire) — doc §Gaps #5;
//   - outbound delivery state — doc §Gaps #3.

import type { LiveRun, LiveRunsResponse, DaemonMood } from './liveRuns.ts';
import { liveRunDisplayName, liveRelicChips, runCourse, type LiveRelicChip } from './liveRuns.ts';
import type { RunLedgerRow, RunLedgerResponse } from './runLedger.ts';
import { relicCounts } from './runLedger.ts';

// ── PROCESS: places ─────────────────────────────────────────────────────────

/** Where an actor stands. Resource places anchor to world identity
 * (chamber/rig/dock); control places are the fixed machine stations
 * (desk/chart/bay/watch). The doc's rule: noun + verb, never verb alone. */
export type PlaceKind =
	// resource places
	| 'chamber' // in the tree: orient/mutate at a path
	| 'test-rig' // local probe attached to the camp
	| 'forge-dock' // gh/git remote operations, remote CI
	// control places
	| 'correspondence-desk' // inbox/portal reads, brnrd do dispositions
	| 'chart-table' // .card / control-file edits
	| 'strand-bay' // dispatch
	| 'watch-point' // wait, tethered to what's awaited
	| 'wake-dock' // starting: the wake is being assembled
	| 'cut-line'; // closing: the boundary out of tense

export interface Place {
	kind: PlaceKind;
	/** The noun: tree path, or the awaited/dispatched thing. Null when the
	 *  wire didn't say — rendered as the bare place, never a guessed path. */
	label: string | null;
}

// Detail-classifier vocabularies. These refine the daemon's own act class
// (hooks.py's six) with the *resource* half the doc requires; they match on
// the already-redacted detail summary, so they are heuristics — each is
// deliberately conservative, and a miss falls back to the act's default
// place rather than an exotic one.
const FORGE_RE = /\bgh (pr|issue|api|run|release|repo)\b|\bgit push\b|\bgit fetch\b|\bgit pull\b/;
const CONTROL_FILE_RE =
	/\.card\b|\.promises\b|\.name\b|\.topics\b|\.mood\b|\.keepalive\b|menu\.json/;
const DESK_RE = /inbox\.json|portal-state\.json|\bbrnrd (do|await)\b|\boutbox\b/;
const KB_RE = /\bkb\/|\.brnrd-kb\/|knowledge\//;

/** The resource anchor for a tree place: prefer the boundary's own attested
 * dir; `.` means the tree root, which we name by the camp (branch) instead. */
function chamberLabel(edge: LiveRun['edge'], room: LiveRun['room']): string | null {
	const dir = edge?.dir && edge.dir !== '.' ? edge.dir : null;
	return dir ?? room?.dir ?? null;
}

/**
 * resolvePlace — the doc's `resource anchor + operation => place`.
 *
 * Lifecycle outranks the edge (a closing run's last boundary is history);
 * then the act picks the place family and the redacted detail refines the
 * resource. Unknown/absent acts resolve to the camp's chamber: an actor
 * with no attested boundary still *exists somewhere*, and the camp is the
 * one place the room block attests (`room.branch`/`room.dir`).
 */
export function resolvePlace(run: Pick<LiveRun, 'edge' | 'room' | 'lifecycle'>): Place {
	if (run.lifecycle === 'starting') return { kind: 'wake-dock', label: null };
	if (run.lifecycle === 'closing') return { kind: 'cut-line', label: null };
	const edge = run.edge;
	const detail = edge?.detail ?? '';
	switch (edge?.act) {
		case 'wait':
			return { kind: 'watch-point', label: edge.detail };
		case 'dispatch':
			return { kind: 'strand-bay', label: edge.detail };
		case 'publish':
			if (DESK_RE.test(detail)) return { kind: 'correspondence-desk', label: edge.detail };
			// publish is egress by class; the forge is its default shore.
			return { kind: 'forge-dock', label: edge.detail };
		case 'probe':
			if (FORGE_RE.test(detail)) return { kind: 'forge-dock', label: edge.detail };
			return { kind: 'test-rig', label: chamberLabel(edge, run.room) ?? edge.detail };
		case 'orient':
			if (DESK_RE.test(detail)) return { kind: 'correspondence-desk', label: edge.detail };
			if (CONTROL_FILE_RE.test(detail)) return { kind: 'chart-table', label: edge.detail };
			if (FORGE_RE.test(detail)) return { kind: 'forge-dock', label: edge.detail };
			if (KB_RE.test(detail)) return { kind: 'chamber', label: 'the library' };
			return { kind: 'chamber', label: chamberLabel(edge, run.room) };
		case 'mutate':
			if (CONTROL_FILE_RE.test(detail)) return { kind: 'chart-table', label: edge.detail };
			if (DESK_RE.test(detail)) return { kind: 'correspondence-desk', label: edge.detail };
			return { kind: 'chamber', label: chamberLabel(edge, run.room) };
		default:
			return { kind: 'chamber', label: chamberLabel(edge, run.room) };
	}
}

// ── the scene ───────────────────────────────────────────────────────────────

export interface RoomActor {
	runId: string;
	name: string;
	/** `@` for the resident thought, `a`…`z` for strands (stable by start
	 *  order within the scene). */
	glyph: string;
	strand: boolean;
	parentRunId: string | null;
	islandLabel: string;
	place: Place;
	act: string | null;
	detail: string | null;
	/** The daemon folded context in at this boundary — traffic to the actor,
	 *  never actor travel (doc §Injection never teleports the actor). */
	injected: boolean;
	lifecycle: string | null;
	awaitUntil: string | null;
	moodRest: string | null;
	course: { done: number; total: number; current: string | null } | null;
	portalsPending: number;
	portalsOldestAt: string | null;
	relics: LiveRelicChip[];
	runner: string | null;
	stale: boolean;
}

export interface RoomCamp {
	branch: string | null;
	/** Worktree dir name; null = the shared checkout (host env). */
	dir: string | null;
	env: string | null;
	actorGlyphs: string[];
}

export interface RoomIsland {
	label: string;
	camps: RoomCamp[];
}

export interface ClothRow {
	runId: string | null;
	name: string;
	tense: 'live' | 'cut';
	glyph: string | null;
	endedAt: string | null;
	wallSeconds: number | null;
	/** Attested money, ledger only — subscription-attributed first, credits
	 *  equivalent second. Null on live rows: live spend is not on the wire
	 *  (doc §Gaps #4), and this model does not interpolate. */
	usd: number | null;
	counts: Record<string, number>;
	course: { done: number; total: number } | null;
	childOf: string | null;
}

export interface RoomGraph {
	generatedAt: string | null;
	islands: RoomIsland[];
	actors: RoomActor[];
	cloth: ClothRow[];
	/** Letters resting at the gate across all live runs — count only; the
	 *  wire carries no per-event identity (doc §Gaps #2). */
	pendingLetters: number;
	daemonMood: DaemonMood | null;
	stale: boolean;
}

const STRAND_GLYPHS = 'abcdefghijklmnopqrstuvwxyz';

function startKey(run: LiveRun): number {
	const t = run.started_at ? Date.parse(run.started_at) : NaN;
	return Number.isNaN(t) ? Infinity : t;
}

function ledgerUsd(row: RunLedgerRow): number | null {
	const sub = row.usd_subscription_attributed;
	if (typeof sub === 'number' && Number.isFinite(sub)) return sub;
	const cred = row.usd_credits_equivalent;
	if (typeof cred === 'number' && Number.isFinite(cred)) return cred;
	return null;
}

/**
 * Compile the scene. Pure; tolerant of either wire being absent (a signed-out
 * ledger, a pre-upgrade daemon). Live actors sort resident-first then by
 * start; strands get stable letter glyphs in that order. Islands are compiled
 * from live camps first, then from recent ledger rows so a dormant account
 * (stage 0) still has ground — durable world without inventing a body.
 */
export function compileRoomGraph(
	live: LiveRunsResponse | null,
	ledger: RunLedgerResponse | null
): RoomGraph {
	const runs = (live?.runs ?? []).filter((r) => r.daemon_stale !== true);
	const residents = runs.filter((r) => !r.is_subspawn).sort((x, y) => startKey(x) - startKey(y));
	const strands = runs.filter((r) => r.is_subspawn).sort((x, y) => startKey(x) - startKey(y));

	const glyphByRun = new Map<string, string>();
	for (const r of residents) glyphByRun.set(r.run_id, '@');
	strands.forEach((s, i) => glyphByRun.set(s.run_id, STRAND_GLYPHS[i % STRAND_GLYPHS.length]));

	const actors: RoomActor[] = [...residents, ...strands].map((run) => ({
		runId: run.run_id,
		name: liveRunDisplayName(run),
		glyph: glyphByRun.get(run.run_id) ?? '?',
		strand: !!run.is_subspawn,
		parentRunId: run.parent_run_id,
		islandLabel: run.repo_label || 'unknown repo',
		place: resolvePlace(run),
		act: run.edge?.act ?? null,
		detail: run.edge?.detail ?? null,
		injected: !!run.edge?.injected,
		lifecycle: run.lifecycle ?? null,
		awaitUntil: run.await_until ?? null,
		moodRest: run.mood_rest ?? run.mood_glyph ?? null,
		course: runCourse(run.card_text),
		portalsPending: run.portals?.pending ?? 0,
		portalsOldestAt: run.portals?.oldest_at ?? null,
		relics: liveRelicChips(run.relics_counts),
		runner: run.runner?.name ?? run.runner?.core ?? null,
		stale: run.daemon_stale === true
	}));

	// WORLD: islands from live camps, then ledger ground for dormant repos.
	const islands = new Map<string, Map<string, RoomCamp>>();
	const campKey = (branch: string | null, dir: string | null) => `${branch ?? ''}::${dir ?? ''}`;
	for (const run of [...residents, ...strands]) {
		const label = run.repo_label || 'unknown repo';
		const camps = islands.get(label) ?? new Map<string, RoomCamp>();
		islands.set(label, camps);
		const branch = run.room?.branch ?? null;
		const dir = run.room?.dir ?? null;
		const key = campKey(branch, dir);
		const camp = camps.get(key) ?? { branch, dir, env: run.room?.env ?? null, actorGlyphs: [] };
		camp.actorGlyphs.push(glyphByRun.get(run.run_id) ?? '?');
		camps.set(key, camp);
	}
	for (const row of ledger?.rows ?? []) {
		const label = row.repo_label;
		if (!label || islands.has(label)) continue;
		islands.set(label, new Map()); // durable ground, no camp, no actor
	}

	// TIME: live rows first (same identity as the walking actor), then cut
	// rows newest-first. One run, one row — a live run also present in the
	// ledger (already cut, presence not yet pruned) keeps the cut row.
	const cloth: ClothRow[] = [];
	const cutIds = new Set((ledger?.rows ?? []).map((r) => r.run_id).filter(Boolean));
	for (const run of [...residents, ...strands]) {
		if (cutIds.has(run.run_id)) continue;
		const course = runCourse(run.card_text);
		cloth.push({
			runId: run.run_id,
			name: liveRunDisplayName(run),
			tense: 'live',
			glyph: glyphByRun.get(run.run_id) ?? '?',
			endedAt: null,
			wallSeconds: null,
			usd: null,
			counts: Object.fromEntries(liveRelicChips(run.relics_counts).map((c) => [c.kind, c.count])),
			course: course ? { done: course.done, total: course.total } : null,
			childOf: run.parent_run_id
		});
	}
	for (const row of ledger?.rows ?? []) {
		cloth.push({
			runId: row.run_id,
			name: row.name || row.run_id || 'run',
			tense: 'cut',
			glyph: null,
			endedAt: row.ended_at,
			wallSeconds: row.wall_clock_seconds,
			usd: ledgerUsd(row),
			counts: relicCounts(row.external_refs ?? []),
			course: null,
			childOf: row.is_subspawn ? (row.parent_run_id ?? null) : null
		});
	}

	return {
		generatedAt: live?.generated_at ?? ledger?.generated_at ?? null,
		islands: [...islands.entries()]
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([label, camps]) => ({ label, camps: [...camps.values()] })),
		actors,
		cloth,
		pendingLetters: actors.reduce((n, a) => n + a.portalsPending, 0),
		daemonMood: live?.daemon_mood ?? null,
		stale: live?.stale ?? false
	};
}
