import type { WithheldLane } from './withheld';

// Slice 3 (kb/design-dashboard-live-surface.md "Reconsidered 2026-07-06"):
// account-scoped live/coexisting-runs view. Types mirror the JSON
// `GET /v1/dashboard/live-runs` returns (`src/brnrd/routers/dashboard.py::
// dashboard_live_runs_api`), sourced from the local presence registry
// (`src/brr/presence.py`) via the daemon's `PUT /v1/daemons/live-runs` publish.

export interface LiveRunRunner {
	name?: string;
	shell?: string;
	core?: string;
	class?: string;
}

export interface LiveRun {
	id: string;
	kind: string;
	stream: string;
	label: string;
	name: string;
	run_id: string;
	repo_label: string;
	started_at: string | null;
	last_seen: string | null;
	// Same join key as the closed-run ledger's `parent_run_id`/`is_subspawn`
	// (run_ledger.py) — a concurrent `spawn:` child carries these while
	// still live, so a peer card can be told apart from a resident thought
	// before it ever reaches the ledger
	// (kb/design-multi-workstream-concurrency.md "Ranked moves" #1).
	parent_run_id: string | null;
	is_subspawn: boolean;
	// Which Shell+Core this thought is running on
	// (`cloud.py::_runner_payload`, same shape Activity/respawn rows already
	// carry) — sourced from the presence entry now that `presence.register`
	// records it at registration time. `{}` on an entry from before this
	// field shipped, or an ad-hoc session that never selected a Runner.
	runner: LiveRunRunner;
	// #200's remaining slice (progress-card richness): the run's current
	// lifecycle phase (`queued`/`preparing`/`running`/`finalizing`/...,
	// `src/brr/run_progress.py::PHASES`) and the live `.card` note text,
	// folded into the same publish tick via `run_progress.project_run`.
	// `null` when there's no conversation record yet or the run hasn't
	// written a card note. Budget/keepalive posture is a separate,
	// not-yet-built slice — nothing persists that state today.
	phase: string | null;
	card_text: string | null;
	course?: RunCourse | null;
	card_updated_at: string | null;
	// #342 relics-so-far: counts of the run's attested produce mid-flight
	// (`{commit: 2, kb: 1}`), from the daemon's heartbeat-refreshed portal
	// capsule via `cloud.py::_live_runs_snapshot`. `null`/absent = nothing
	// attested (ad-hoc session, pre-upgrade daemon); `{}` = known, no
	// produce yet. Render via `liveRelicChips` below.
	relics_counts?: Record<string, number> | null;
	// #566 slice 0: the resident-authored mood — raw handle from the run's
	// `.mood` control file, glyph/pitch resolved daemon-side against
	// `brr.emotes` so the frontend owns no emote table. All null/absent when
	// unset; an unknown handle arrives name-only (render the bare name,
	// never a guessed face — the emote library's honesty bar).
	mood?: string | null;
	mood_glyph?: string | null;
	// Every breath the face can take: base→expression→base sequences, primary
	// first (`Emote.sequences`). `mood_glyph` is only the *resting* frame —
	// which is why a run's mood used to be unanimatable here, and why 61 of
	// the 98 situational emotes arrived looking identical. Absent on a
	// pre-upgrade daemon; render `mood_glyph` still, just without motion.
	mood_frames?: string[][] | null;
	/** The frame a resting surface holds. Per emote, unlike `mood_glyph`
	 *  (= the animation's base, shared across a face family). Absent on a
	 *  pre-upgrade daemon, and absent for a face whose distinct resting
	 *  frame hasn't been authored yet — both fall back to the base. */
	mood_rest?: string | null;
	mood_pitch?: number | null;
	// The run's claimed topic slugs (the-run-that-claims-its-thread): raw
	// from the resident's `.topics` control via the presence heartbeat —
	// a burning run's thread, visible while it burns, before any
	// `topics.md` lands on the node. Absent on a pre-upgrade daemon or an
	// unclaimed run; resolve against the page's own warp graph
	// (`topicFaces`), never a table of this file's own.
	topics?: string[] | null;
	// #476 wyrd §3: a stop the account owner has parked for this run, not yet
	// consumed by the daemon. Server-side (rather than a fact the client holds
	// in memory) so the cell keeps saying "stopping" across a reload — and so
	// it says only that: the run is still running until the daemon's next sync
	// finalizes it as `stopped`.
	stop_requested?: boolean;
	// the-overlay-that-shows-the-room: where the work happens, published by
	// the daemon (`cloud_publisher._room_payload` / `_edge_payload` /
	// `_lifecycle_payload`) rather than guessed here. All absent on a
	// pre-upgrade daemon or an ad-hoc session — absent stays absent.
	//
	// `lifecycle` is the derived execution state: starting | weaving |
	// awaiting | closing. AWAITING is a positive fact (the run's own portal
	// `await` facet, armed and unresolved) — never an inference from
	// quietness — and CLOSING is the attested finalizing phase. Both must
	// render *specifically*: an awaiting runner still exists, which is what
	// separates it from "between wakes"; a closing run is a boundary in
	// flight, not a place to linger (design-resident-field.md §Lifecycle).
	lifecycle?: string | null;
	/** The armed wait's deadline (ISO), when lifecycle is `awaiting`. */
	await_until?: string | null;
	/** Where this thought's hands are: env kind, the branch the tree is
	 *  actually on (asked of git live — a run renames branches mid-flight),
	 *  and the worktree dir name (`null` dir = the shared checkout). */
	room?: LiveRunRoom | null;
	/** The latest attested tool boundary, from the run's boundary
	 *  transcript tail: classified act, tool names, the already-redacted
	 *  detail summary (secrets masked at write time, hooks._tool_detail),
	 *  response bytes, and whether the daemon injected context there. */
	edge?: LiveRunEdge | null;
	/** THE CROSSINGS — the boundaries that carried an injection, newest first,
	 *  bounded daemon-side at 8 (`cloud_publisher._CROSSINGS_MAX`).
	 *
	 *  Distinct from `edge` for one reason: `edge` is a **cursor**, whichever
	 *  boundary was current at publish time. This page polls on an interval,
	 *  so two injections inside one window meant one was never published —
	 *  and a "read" count counted polls that landed rather than crossings
	 *  (measured 2026-08-28). A cursor cannot be sampled into a stream, so
	 *  the stream is published as one.
	 *
	 *  Empty is a real answer: nothing crossed since the transcript tail
	 *  began. Absent on a daemon predating the field, which is a different
	 *  fact and stays one — a client that required it would show every
	 *  un-upgraded daemon as having never received a message. */
	crossings?: LiveRunEdge[] | null;
	/** Pending correspondence at the run's portal — the message ceremony's
	 *  *resting, put to read* state. `null`/absent = no portal attested
	 *  (ad-hoc session, pre-upgrade daemon); `pending: 0` = a known-empty
	 *  door. */
	portals?: LiveRunPortals | null;
	// #1510 ("the mood of a dead run"): this row's own source report is older
	// than the freshness window — server-computed (`dashboard.py::
	// _stamp_row_freshness`), same shape as `RunnerProfile.daemon_stale`
	// (`runners.ts`) and `QuotaShell.daemon_stale` (`quota.ts`). `_live_runs_views`
	// merges by `run_id` across every daemon on the account, freshest report
	// per key wins — a run reported by a daemon that then retires is keyed
	// only by its own `run_id`, so no live daemon ever overwrites it and it
	// merge-survives indefinitely, frozen at whatever it last reported. Any
	// consumer picking a leading/best row out of `runs` (`latestRunMood`,
	// `pickLane.ts::pickRows`) must skip a row with this true, or a dead run's
	// stale data can win the pick forever.
	daemon_stale?: boolean | null;
}

export interface LiveRunRoom {
	env: string | null;
	branch: string | null;
	dir: string | null;
}

/** Correspondence waiting at the run's portal — the *put to read* fact of
 * the message ceremony (`cloud_publisher._portals_payload`). Counts and one
 * timestamp only; a pending body never rides this wire. */
export interface LiveRunPortals {
	pending: number;
	oldest_at: string | null;
}

export interface LiveRunEdge {
	at: string | null;
	phase: string | null;
	act: string | null;
	tools: string[];
	detail: string | null;
	out_bytes: number | null;
	injected: boolean;
	/** Where the act ran, relative to the run's own tree (`.` = the tree
	 *  root) — relativized daemon-side; a host path never rides the wire. */
	dir?: string | null;
}

export interface LiveRunsResponse {
	generated_at: string;
	runs: LiveRun[];
	stale: boolean;
	reported_at: string | null;
	// Configured `spawn:` pool width (`spawn.max_concurrent`), piggybacked
	// on this same publish tick — loom-envelope Phase 1's one piece of data
	// the slice-1 publish didn't already carry. `null` before any daemon
	// has reported it (pre-upgrade daemon, or never published yet).
	spawn_max_concurrent: number | null;
	// #566 slice 0: the daemon-level telemetry face — what the board wears
	// when no run is live (`cloud.py::_daemon_mood_payload`; today `idle` |
	// `running`, richer states later). Feeds the loom NOW seam and the
	// wordmark at rest. `null` before any daemon has reported it.
	daemon_mood?: DaemonMood | null;
	withheld?: WithheldLane;
}

export interface DaemonMood {
	state: string;
	name: string;
	glyph: string;
	frames: string[];
	/** Alternates, same shape as `LiveRun.mood_frames`. Absent pre-upgrade,
	 *  in which case `frames` is the only cycle there is. */
	sequences?: string[][] | null;
	rest?: string | null;
	pitch: number;
}

/**
 * Resident-authored name wins, then the conversation key, then the kind.
 *
 * The middle of this chain used to be "the waking-message excerpt" — and
 * that excerpt was a run's verbatim task body, which #585 removed at the
 * producer: a presence label is dashboard chrome, not a content channel
 * into every sibling's model context. `label` stays in the chain because a
 * deliberate, handle-shaped label is still legal; it is simply empty now
 * for a run that hasn't authored a `.name`.
 *
 * `stream` (the conversation key) is the new middle rung, matching the
 * precedence `facets.py::_sibling_handle` already uses on the other
 * consumer of the same presence entry. Without it a card with no `.name`
 * yet falls straight through to `kind` and every live run on the board
 * reads "daemon" — the leak closed, and the panel's legibility with it.
 */
export function liveRunDisplayName(
	run: Pick<LiveRun, 'name' | 'label' | 'kind'> & Partial<Pick<LiveRun, 'stream'>>
): string {
	return run.name || run.label || run.stream || run.kind || 'run';
}

// Render order for relics-so-far chips — produce first, chatter last.
// Mirrors `brr.relics._TAIL_NOUNS` order (hand-mirrored, same precedent as
// RELIC_ICONS in runLedger.ts). `branch` is deliberately absent: mid-flight
// every commit-bearing run has exactly one branch, so a branch chip only
// restates the commits chip (#329's family logic makes the same call on
// receipts). `summary` is prose, not produce.
const RELIC_CHIP_ORDER = [
	'commit',
	'merge',
	'pr',
	'issue',
	'kb',
	'file',
	'comment',
	'message',
	'reply'
];
const RELIC_CHIP_EXCLUDE = new Set(['branch', 'summary']);

export interface LiveRelicChip {
	kind: string;
	count: number;
}

/** Relics-so-far counts → ordered chips for the expanded live-run card
 * (#342). Zero/absent counts → `[]`, so the card renders no relics row at
 * all. Unknown kinds trail in alphabetical order rather than vanishing —
 * the backend's relic vocabulary is meant to grow without a frontend
 * round trip (same posture as `RelicRecord`). */
export function liveRelicChips(counts: Record<string, number> | null | undefined): LiveRelicChip[] {
	if (!counts) return [];
	const chips: LiveRelicChip[] = [];
	for (const kind of RELIC_CHIP_ORDER) {
		const count = counts[kind] ?? 0;
		if (count > 0) chips.push({ kind, count });
	}
	for (const kind of Object.keys(counts).sort()) {
		if (RELIC_CHIP_ORDER.includes(kind) || RELIC_CHIP_EXCLUDE.has(kind)) continue;
		const count = counts[kind] ?? 0;
		if (count > 0) chips.push({ kind, count });
	}
	return chips;
}

/**
 * A mood as any surface renders it: a name, and *maybe* a glyph.
 *
 * The one house rule, and it comes from the emote library's own docstring: an
 * unknown or absent mood renders as NOTHING or the bare handle — never a
 * guessed or default face. So this frontend owns no emote table. The glyph is
 * whatever the daemon resolved against `brr.emotes` and put on the wire; a
 * handle the library doesn't know arrives name-only and stays name-only here.
 * No name at all is not a mood, and the surfaces render nothing.
 */
export interface MoodFace {
	name: string;
	glyph: string | null;
	/** The face's breaths, primary first — each `base → expression → base`.
	 *  Null when the wire carried none (unknown handle, or a daemon older
	 *  than `mood_frames`), and then the face simply doesn't move. */
	sequences: string[][] | null;
	/** The frame to hold while still — see `MoodChip`. Null ⇒ the surface
	 *  falls back to the animation's base, which is shared, which is why
	 *  this field exists. */
	rest: string | null;
	pitch: number | null;
}

/** Drop empties at both levels so a caller can trust `sequences?.length`.
 *  The wire bounds hostile payloads; this bounds *meaningless* ones — a
 *  `[[]]` is not a cycle, and a renderer shouldn't have to know that. */
function cleanSequences(raw: string[][] | null | undefined): string[][] | null {
	if (!Array.isArray(raw)) return null;
	const out = raw
		.filter((seq): seq is string[] => Array.isArray(seq))
		.map((seq) => seq.filter((frame) => typeof frame === 'string' && frame.trim()))
		.filter((seq) => seq.length > 0);
	return out.length > 0 ? out : null;
}

/** Normalize a wire mood into a `MoodFace`, or `null` for "no mood". */
export function moodFace(
	name: string | null | undefined,
	glyph?: string | null,
	pitch?: number | null,
	frames?: string[][] | null,
	rest?: string | null
): MoodFace | null {
	const handle = (name ?? '').trim();
	if (!handle) return null;
	return {
		name: handle,
		glyph: (glyph ?? '').trim() || null,
		sequences: cleanSequences(frames),
		rest: (rest ?? '').trim() || null,
		pitch: typeof pitch === 'number' && Number.isFinite(pitch) ? pitch : null
	};
}

/** The mood of the most recently started live run that has one. The wordmark
 *  wears one face, and a board with several burning runs should show the
 *  newest thought's — that's the one whose state the reader is watching
 *  change. Runs with an unparseable `started_at` sort oldest rather than
 *  winning by accident.
 *
 *  #1510: a row with `daemon_stale === true` is skipped outright, never a
 *  candidate. `_live_runs_views` merges by `run_id` across every daemon on
 *  the account, freshest report per key wins — a run reported by a daemon
 *  that then retires merge-survives indefinitely, frozen at whatever
 *  `started_at` it last had. Comparing `started_at` alone lets a dead run
 *  with a newer wall-clock start beat a genuinely live one forever; skipping
 *  stale rows is the same guarantee `readTanks`/`isTappable` already give
 *  the quota tank and the spool rack. Every live run stale ⇒ no candidate at
 *  all ⇒ `null`, which is honest: nothing here is known to be live right
 *  now, and `wordmarkMood` below already falls back to the daemon's own
 *  resting face in that case. */
export function latestRunMood(runs: LiveRun[] | null | undefined): MoodFace | null {
	let best: LiveRun | null = null;
	let bestAt = -Infinity;
	for (const run of runs ?? []) {
		if (run.daemon_stale === true) continue;
		if (!moodFace(run.mood, run.mood_glyph, run.mood_pitch, run.mood_frames, run.mood_rest))
			continue;
		const started = run.started_at ? Date.parse(run.started_at) : NaN;
		const at = Number.isNaN(started) ? -Infinity : started;
		if (best === null || at > bestAt) {
			best = run;
			bestAt = at;
		}
	}
	return best
		? moodFace(best.mood, best.mood_glyph, best.mood_pitch, best.mood_frames, best.mood_rest)
		: null;
}

/** What the header wordmark animates: a live run's mood when one is burning,
 *  else the daemon's resting face, else nothing (pre-upgrade daemon, or a
 *  resident that never set a mood — the wordmark keeps its built-in wink).
 *
 *  A run's mood now carries its whole cycle (`mood_frames`), so the resident's
 *  authored face animates here exactly as the daemon's derived one always did;
 *  the single-glyph path below is the pre-upgrade-daemon fallback, not the
 *  normal case. An unknown handle resolves to no frames at all, which is why
 *  this can return a pitch with null frames: the tint is still honest
 *  telemetry when the face isn't. */
export function wordmarkMood(
	runs: LiveRun[] | null | undefined,
	daemonMood: DaemonMood | null | undefined
): { frames: string[] | null; pitch: number | null } {
	const live = latestRunMood(runs);
	if (live) {
		// The primary cycle. The wordmark wears one face at a time and its own
		// choreography already varies the hold, so alternates are the chip's
		// business, not the mark's.
		const cycle = live.sequences?.[0] ?? (live.glyph ? [live.glyph] : null);
		return { frames: cycle, pitch: live.pitch };
	}
	if (!daemonMood) return { frames: null, pitch: null };
	// Unlike the chip, the wordmark doesn't need a *name* — it renders the
	// motion, not the label — so the daemon branch reads frames and pitch
	// directly. Neither is a guess: both came off the wire already resolved.
	const frames = (daemonMood.frames ?? []).filter((frame) => frame && frame.trim());
	const pitch = daemonMood.pitch;
	return {
		frames: frames.length > 0 ? frames : null,
		pitch: typeof pitch === 'number' && Number.isFinite(pitch) ? pitch : null
	};
}

export class LiveRunsAuthError extends Error {}

/** Fetches the account-scoped live-runs snapshot. Throws `LiveRunsAuthError`
 * on a 401 (no session cookie), same shape as `fetchQuota`. */
export async function fetchLiveRuns(fetchImpl: typeof fetch = fetch): Promise<LiveRunsResponse> {
	const res = await fetchImpl('/v1/dashboard/live-runs', { credentials: 'include' });
	if (res.status === 401) {
		throw new LiveRunsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`live-runs fetch failed: ${res.status}`);
	}
	return (await res.json()) as LiveRunsResponse;
}

/** A parked run stop (#476 wyrd §3). Not cancelable: by the time the row
 * exists the only thing between it and a dead process is one daemon sync. */
export interface RunStopRequest {
	request_id: string;
	run_id: string;
	requested_at: string | null;
	status: string;
}

/** Ask the daemon to stop a burning run. Async by nature — this parks the
 * request; the daemon consumes it on its next sync and the run finalizes as
 * `stopped` with partial work salvaged. */
export async function requestRunStop(
	runId: string,
	fetchImpl: typeof fetch = fetch
): Promise<RunStopRequest> {
	const res = await fetchImpl(`/v1/dashboard/runs/${encodeURIComponent(runId)}/stop`, {
		method: 'POST',
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new LiveRunsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(
			res.status === 404 ? 'that run is no longer live' : `stop failed: ${res.status}`
		);
	}
	return ((await res.json()) as { stop_request: RunStopRequest }).stop_request;
}

/** Heartbeat freshness → lifecycle temperature. A heartbeat lands roughly
 * every 30s (`daemon.py`'s watch loop); three missed beats reads as genuinely
 * stalling rather than one slow tick. The registry itself only prunes at 300s
 * (`presence.DEFAULT_STALE_AFTER_S`), so a run can sit "stalling" for a while
 * before it's gone — that gap is real and worth seeing. Shared by the
 * LiveRuns grid and the inline node panel so the two surfaces cannot disagree
 * about whether one run is alive. */
export const STALL_AFTER_MS = 90_000;

export type HeartbeatLevel = 'running' | 'stalling' | 'unknown';

export function heartbeatLevel(
	lastSeen: string | null,
	now: number,
	stale: boolean
): HeartbeatLevel {
	if (stale) return 'unknown';
	const seen = lastSeen ? Date.parse(lastSeen) : NaN;
	if (Number.isNaN(seen)) return 'unknown';
	return now - seen > STALL_AFTER_MS ? 'stalling' : 'running';
}

// ── where the work happens (the-overlay-that-shows-the-room) ────────────────

/** The lifecycle states that deserve their own word on a surface. `weaving`
 * is the ordinary burning state the phase label already narrates, so it
 * returns `null` here — this helper answers only "is this run in a state a
 * reader must not mistake for ordinary work": AWAIT (the runner exists and
 * is deliberately holding — not between wakes, not stalled) and CLOSING
 * (the closeout boundary is in flight). `starting` gets a word too: a
 * registered thought whose Shell hasn't spoken yet. */
export function lifecycleNotice(
	run: Pick<LiveRun, 'lifecycle' | 'await_until'>
): { word: string; tone: 'starting' | 'awaiting' | 'closing'; detail: string | null } | null {
	switch (run.lifecycle) {
		case 'starting':
			return { word: 'starting', tone: 'starting', detail: 'the wake is being assembled' };
		case 'awaiting': {
			const until = run.await_until ? Date.parse(run.await_until) : NaN;
			const deadline = Number.isNaN(until)
				? null
				: new Date(until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
			return {
				word: 'await',
				tone: 'awaiting',
				detail: deadline
					? `holding for the world — resolves on any event, or ${deadline}`
					: 'holding for the world — resolves on any event'
			};
		}
		case 'closing':
			return { word: 'closing', tone: 'closing', detail: 'closeout in flight — attesting produce' };
		default:
			return null;
	}
}

/** One line naming the room: `branch · dir` (worktree) or `branch · checkout`
 * (host). `null` when the daemon published no room — absent stays absent,
 * never a guessed path. */
export function roomLine(room: LiveRunRoom | null | undefined): string | null {
	if (!room) return null;
	const parts = [room.branch, room.dir ?? (room.env === 'host' ? 'the shared checkout' : null)];
	const line = parts.filter(Boolean).join(' · ');
	return line || null;
}

/** The latest boundary as one compact line: `act · detail · in <dir>`. The
 * detail is already redacted and capped at the writer (`hooks._tool_detail`);
 * the dir arrives tree-relative from the publisher; this only composes. */
export function edgeLine(edge: LiveRunEdge | null | undefined): string | null {
	if (!edge) return null;
	const where = edge.dir && edge.dir !== '.' ? `in ${edge.dir}` : null;
	const parts = [edge.act, edge.detail, where].filter(Boolean);
	return parts.length ? parts.join(' · ') : null;
}

/** Course position parsed from the run's own card: `- [ ]` / `- [x]` rows
 * anywhere in the card text (the resident's `## Plan` convention). Returns
 * `null` when the card carries no checkbox course at all — a run without a
 * course renders nothing rather than `0/0`. `current` is the first open
 * row, the reader's "where the plan is standing". */
export interface RunCourse {
	done: number;
	total: number;
	current: string | null;
}

export function runCourse(
	cardText: string | null | undefined,
	published?: RunCourse | null
): RunCourse | null {
	if (published) return published;
	if (!cardText) return null;
	let done = 0;
	let total = 0;
	let current: string | null = null;
	for (const line of cardText.split('\n')) {
		const match = /^\s*[-*] \[([ xX])\]\s+(.*)$/.exec(line);
		if (!match) continue;
		total += 1;
		if (match[1] === ' ') {
			if (current === null) current = match[2].trim();
		} else {
			done += 1;
		}
	}
	return total > 0 ? { done, total, current } : null;
}
