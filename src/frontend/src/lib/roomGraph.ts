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
import type { ScheduledWakesResponse } from './scheduledWakes.ts';
import type { QuotaResponse } from './quota.ts';
import { fuelProviderGroups } from './fuelProviders.ts';

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

/**
 * The repo-relative directory a boundary puts the actor in.
 *
 * **TERRAIN GROWS FROM TERRAIN.** The cwd the daemon attested is always
 * terrain — `_edge_dir` resolved it against the run's real checkout before
 * publishing. A path merely *named in the detail* is terrain only when it
 * **extends ground already attested**: a prefix-extension of a chamber some
 * real cwd, or this run's own trail, already put on the map.
 *
 * That property is what the two previous rules were reaching for and missing.
 *
 * - The first matched any relative token with three or more segments. Three
 *   segments is a *shape*, and the shapes kept arriving: `0.4/0.3/0.2` (a
 *   version or an opacity ramp) and `origin/brr/the-fuel…` (a git ref) both
 *   grew chambers on the live map, as `~/.local/state/brnrd/…` had before
 *   them — that one matched because the account home carries the project's
 *   own name, and it was patched with a hidden-directory fence. Each fix met
 *   the next shape nobody listed.
 * - The second (2026-08-28) removed detail-derived terrain altogether. That
 *   ends the fakes and re-opens the gap the first was built for, in its own
 *   deleted words: *"most acts run from the root and name their true location
 *   only in the detail — a dozen edits into `src/frontend` grew zero
 *   terrain."* A trie that only grows on `cd` barely grows.
 *
 * Accretion answers both. `0.4/0.3/0.2` cannot extend attested ground under
 * any account, without anyone having to list it; `src/frontend/src/lib`
 * extends `src` the moment a real cwd attested `src`. And it is the growth
 * model the room already wants: the island spreads outward from where the
 * resident has actually stood.
 *
 * Everything that fails the test is still real and stays on the actor's own
 * boundary line — a forge interaction, a kb write, an out-of-tree path. Not
 * terrain, not discarded.
 */
export function dirFromEdge(edge: LiveRun['edge'], attested?: Iterable<string>): string | null {
	const cwd = edge?.dir && edge.dir !== '.' ? edge.dir : null;
	if (cwd) return cwd;
	const detail = edge?.detail;
	if (!detail || !attested) return null;
	// Roots to grow from, longest first, so the deepest attested ground wins.
	const roots = [...attested].filter(Boolean).sort((a, b) => b.length - a.length);
	if (roots.length === 0) return null;
	let best: string | null = null;
	for (const match of detail.matchAll(/(?<=^|[\s'"(=])[\w.@-]+(?:\/[\w.@-]+)+/g)) {
		const token = match[0];
		// A leaf that looks like a file is not a chamber; chambers are dirs.
		const segs = token.split('/').filter(Boolean);
		const last = segs[segs.length - 1];
		const dirSegs = last && /\.[A-Za-z][A-Za-z0-9]{0,6}$/.test(last) ? segs.slice(0, -1) : segs;
		if (dirSegs.length === 0) continue;
		const dir = dirSegs.join('/');
		// The accretion test: this must be attested ground, or ground directly
		// beneath it. `src/frontend` extends `src`; `origin/brr` extends nothing.
		const grows = roots.some((root) => dir === root || dir.startsWith(root + '/'));
		if (!grows) continue;
		if (best === null || dir.length > best.length) best = dir;
	}
	return best;
}

/** The resource anchor for a tree place: prefer the boundary's attested cwd;
 * the camp names the known checkout/worktree root otherwise. */
function chamberLabel(edge: LiveRun['edge'], room: LiveRun['room']): string | null {
	return dirFromEdge(edge) ?? room?.dir ?? null;
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
export function resolvePlace(
	run: Pick<LiveRun, 'edge' | 'room' | 'lifecycle'> & Partial<Pick<LiveRun, 'repo_label'>>
): Place {
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
			return {
				kind: 'test-rig',
				label: chamberLabel(edge, run.room) ?? edge.detail
			};
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
	/** The chamber the previous distinct footstep stood in — the relocation
	 *  the current boundary made, drawn as travel. Null when unknown or the
	 *  actor hasn't moved chambers. */
	cameFrom: string | null;
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

/** A chamber of the tree this camp's work has actually touched — accreted
 * from attested boundaries only ("only what you touch comes into being").
 * The island grows terrain from the trail, never from a directory listing. */
export interface CampChamber {
	dir: string;
	lastAct: string | null;
	/** The last file the work touched here, parsed from the redacted detail —
	 *  the leaf on the branch the reader actually watches move. */
	lastFile: string | null;
	visits: number;
}

/** The file a boundary's detail names, when one is legible: the last token
 * that looks like a filename with an extension, or a dotfile (`.card`,
 * `.keepalive` — the control files are exactly the acts that were invisible
 * before the pager ceremony). Conservative — no match, no leaf. */
export function fileFromDetail(detail: string | null | undefined): string | null {
	if (!detail) return null;
	const matches = detail.match(
		/(?<=^|[\s'"(/])\.[\w-]{2,}(?=\s|$|['")\]])|[\w][\w.-]*\.[A-Za-z][A-Za-z0-9]{0,6}(?=\s|$|['")\]])/g
	);
	if (!matches || matches.length === 0) return null;
	const last = matches[matches.length - 1];
	// a bare domain or version number is not a file
	if (/^\d+(\.\d+)+$/.test(last)) return null;
	return last.includes('/') ? (last.split('/').pop() ?? null) : last;
}

export interface RoomCamp {
	branch: string | null;
	/** Worktree dir name; null = the shared checkout (host env). */
	dir: string | null;
	env: string | null;
	actorGlyphs: string[];
	chambers: CampChamber[];
	/** Commits attested so far by this camp's actors — material accreting on
	 *  the spur (mid-run identity is doc gap #6; the count is what the wire
	 *  serves). */
	commits: number;
}

/** One attested footstep, remembered by the caller across polls — the model
 * stays pure; the page owns the memory. */
export interface TrailStep {
	dir: string;
	act: string | null;
	at: string | null;
	file?: string | null;
}

export interface RoomIsland {
	label: string;
	camps: RoomCamp[];
	/** Remote-forge produce attested by this island's live actors — PR /
	 *  issue / merge counts for the FORGE dock (his steer, 2026-08-27: the
	 *  forge looked good but the PRs and issues had no place). Counts only:
	 *  the wire attests `relics_counts`, not identities. */
	forge: Record<string, number>;
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

/** A clockwork entry — future intent, never a body on the floor. */
export interface ClockEntry {
	summary: string;
	nextAt: string | null;
	status: string | null;
	repoLabel: string | null;
}

/** One shell's binding provider allowance at the fuel rack. Capacity, not spend. */
export interface FuelRow {
	shell: string;
	status: string;
	/** The named binding provider window. Core allowances never enter this row. */
	windows: { label: string; percent: number | null }[];
	/** How soon the binding window comes back, short form. Carried because a
	 *  ceiling without its clock is only half an instruction: 10% left with
	 *  40 minutes to reset and 10% left with three days are opposite advice,
	 *  and the percentage cannot tell them apart. Null when the wire attested
	 *  no reset instant. */
	resetShort: string | null;
}

/** A watchtower sighting. Every fact resolves to a source — the tower
 *  points, it never owns (doc §Watchtower). Only wire-attested classes
 *  appear; what the wire cannot see (returned strands, promise mismatches)
 *  is absent, not zero. */
export interface WatchFact {
	mark: '◇' | 'T' | '^';
	text: string;
	/** Run id or schedule id the beacon resolves to. */
	source: string;
	/** The armed wait's deadline (ISO) for `^` facts — the same `brnrd
	 *  await` arming the wire attests as `lifecycle: awaiting`. */
	until?: string | null;
}

export interface RoomGraph {
	generatedAt: string | null;
	islands: RoomIsland[];
	actors: RoomActor[];
	cloth: ClothRow[];
	/** Letters resting at the gate across all live runs — count only; the
	 *  wire carries no per-event identity (doc §Gaps #2). */
	pendingLetters: number;
	/** Bodies at work against the pool that bounds them. The wire has carried
	 *  `spawn_max_concurrent` since the loom envelope's phase 1; nothing had
	 *  read it onto the graph, so the room could show a resident and three
	 *  strands without ever saying how many more it could hold. `max` is null
	 *  on a daemon that has not reported one — a different fact from zero. */
	slots: { active: number; max: number | null };
	clockwork: ClockEntry[];
	garage: FuelRow[];
	watch: WatchFact[];
	daemonMood: DaemonMood | null;
	stale: boolean;
}

export interface RoomExtras {
	wakes?: ScheduledWakesResponse | null;
	quota?: QuotaResponse | null;
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
/** The previous distinct chamber in a trail — the relocation's origin. */
function priorChamber(trail: TrailStep[] | undefined, currentDir: string | null): string | null {
	if (!trail || !currentDir) return null;
	for (let i = trail.length - 1; i >= 0; i--) {
		if (trail[i].dir !== currentDir) return trail[i].dir;
	}
	return null;
}

export function compileRoomGraph(
	live: LiveRunsResponse | null,
	ledger: RunLedgerResponse | null,
	trails?: Record<string, TrailStep[]>,
	extras?: RoomExtras
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
		cameFrom: priorChamber(
			trails?.[run.run_id],
			dirFromEdge(
				run.edge,
				(trails?.[run.run_id] ?? []).map((s) => s.dir).filter(Boolean) as string[]
			)
		),
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
		const camp = camps.get(key) ?? {
			branch,
			dir,
			env: run.room?.env ?? null,
			actorGlyphs: [],
			chambers: [],
			commits: 0
		};
		camp.actorGlyphs.push(glyphByRun.get(run.run_id) ?? '?');
		camp.commits += run.relics_counts?.commit ?? 0;
		// terrain accretes from the trail — attested footsteps only, current
		// boundary included so the actor always stands on known ground.
		//
		// The trail is also what the current boundary may grow *from*: a
		// detail-named path counts only when it extends ground a real cwd
		// already put here (see `dirFromEdge`). The run's own root is seeded
		// so the very first `src/frontend/x.ts` after a root-level cwd has
		// something to extend — without it the island could never take its
		// first step off the root, and a trie that only grows on `cd` barely
		// grows at all.
		const steps: TrailStep[] = [...(trails?.[run.run_id] ?? [])];
		const attested = new Set<string>(steps.map((s) => s.dir).filter(Boolean) as string[]);
		if (run.room?.dir && run.room.dir !== '.') attested.add(run.room.dir);
		const edgeDir = dirFromEdge(run.edge, attested);
		const edgeAt = run.edge?.at ?? null;
		const alreadyRecorded = edgeAt !== null && steps.some((s) => s.at === edgeAt);
		if (edgeDir && !alreadyRecorded)
			steps.push({
				dir: edgeDir,
				act: run.edge?.act ?? null,
				at: edgeAt,
				file: fileFromDetail(run.edge?.detail)
			});
		for (const step of steps) {
			if (!step.dir) continue;
			const existing = camp.chambers.find((c) => c.dir === step.dir);
			if (existing) {
				existing.visits += 1;
				if (step.act) existing.lastAct = step.act;
				if (step.file) existing.lastFile = step.file;
			} else {
				camp.chambers.push({
					dir: step.dir,
					lastAct: step.act,
					lastFile: step.file ?? null,
					visits: 1
				});
			}
		}
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

	// CLOCKWORK: future intent from the schedule wire — never a body.
	// No status blocklist here, deliberately, and the reasoning is worth
	// keeping: one was written on 2026-08-28 against
	// `{completed, cancelled, anchoring}` and every term of it was wrong.
	// `anchoring` is not a status at all — it is `scheduled_for === null`
	// (`scheduledWakes.ts:22`), which the camera's own consumer already
	// filters on. And the server's real dead vocabulary is eleven spellings
	// across two sets (`dashboard.py:63-64`: complete/completed/done/
	// responded/success/succeeded, failed/error/errored/cancelled/canceled),
	// so a hand-listed pair caught two of them — the class defined by
	// listing its members, meeting the members nobody listed.
	//
	// The structural property is upstream and already applied:
	// `fetchScheduledWakes` requests `?kind=scheduled`, so a finished run
	// cannot arrive on this wire in the first place. What made a stale entry
	// *look* live was never its status — it was `untilLabel` clamping the
	// countdown to `0m`. That is fixed where it lived, in the label.
	const clockwork: ClockEntry[] = (extras?.wakes?.rows ?? []).map((w) => ({
		summary: w.summary || w.id,
		nextAt: w.scheduled_for,
		status: w.status,
		repoLabel: w.repo_label
	}));

	// GARAGE: one binding provider allowance per shell. `fuelProviderGroups`
	// owns provider/core scope and reads preserved stale values through
	// `quotaWindowReading`; the room keeps that shared interpretation intact.
	const quotaShells = extras?.quota?.runner_quotas ?? [];
	const quotaByShell = new Map(quotaShells.map((shell) => [shell.shell, shell]));
	const garage: FuelRow[] = fuelProviderGroups(quotaShells).map((group) => {
		const shell = quotaByShell.get(group.provider)!;
		const primary = group.primary;
		return {
			shell: group.provider,
			status: shell.daemon_stale === true ? 'stale' : shell.status,
			windows: primary ? [{ label: primary.windowName, percent: primary.percent }] : [],
			resetShort: primary?.resetShort ?? null
		};
	});

	// WATCH: only beacons that resolve to a source. Letters resolve to their
	// run; armed waits resolve to the run that armed them (`brnrd await` →
	// `lifecycle: awaiting` + `await_until` on the wire — the tower is wired
	// to the same mechanism the actor waits with). What the wire cannot see
	// is absent, never a synthesized zero.
	const watch: WatchFact[] = [];
	for (const actor of actors) {
		if (actor.portalsPending > 0)
			watch.push({
				mark: '◇',
				text: `${actor.portalsPending} letter${actor.portalsPending > 1 ? 's' : ''} — ${actor.name}`,
				source: actor.runId
			});
		if (actor.lifecycle === 'awaiting')
			watch.push({
				mark: '^',
				text: `awaiting — ${actor.name}`,
				source: actor.runId,
				until: actor.awaitUntil
			});
	}
	// Schedule entries stay in CLOCKWORK (future intent); the renderer may
	// escalate an overdue one into the tower with its clock in hand — the
	// compile has no `now` and refuses to guess dueness.

	return {
		generatedAt: live?.generated_at ?? ledger?.generated_at ?? null,
		islands: [...islands.entries()]
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([label, camps]) => {
				const forge: Record<string, number> = {};
				for (const actor of actors) {
					if (actor.islandLabel !== label) continue;
					for (const chip of actor.relics) {
						if (chip.kind === 'pr' || chip.kind === 'issue' || chip.kind === 'merge')
							forge[chip.kind] = (forge[chip.kind] ?? 0) + chip.count;
					}
				}
				return { label, camps: [...camps.values()], forge };
			}),
		actors,
		cloth,
		pendingLetters: actors.reduce((n, a) => n + a.portalsPending, 0),
		slots: {
			active: actors.filter((a) => a.strand).length,
			max: live?.spawn_max_concurrent ?? null
		},
		clockwork,
		garage,
		watch,
		daemonMood: live?.daemon_mood ?? null,
		stale: live?.stale ?? false
	};
}
