import type { RelicRecord, RunLedgerRow } from './runLedger';

// The bolt's cloth-side ack store (design-the-bolt.md §The cloth side, fork
// 2 signed: a compact summons strip at the page head → tap jumps to the
// cloth-head lane → the lane glows on arrival — moving sections,
// force-scroll, and a modal are all explicitly rejected).
//
// Per-viewer is the design requirement itself (the teams curveball: an
// away-ack must never be account-global), and localStorage satisfies that
// for the single-user pre-release. Server-side per-viewer state is the
// teams-era successor — noted, not built here.
//
// The storage shape follows the one existing pattern (`publishScope.ts`'s
// `connectPublishScopeStorageKey` + validate-on-read): namespace the key by
// account, and let anything unreadable degrade to "nothing taken" rather
// than throwing — a viewer's local ack state must never break the page it
// lives on.

const BOLT_STORAGE_PREFIX = 'brnrd.bolts.taken';

/** FIFO cap on the stored ack list — bounded so a long-lived account's
 *  local storage entry cannot grow forever; the oldest acks age out first. */
export const BOLT_TAKEN_CAP = 200;

export function boltsTakenStorageKey(accountId: string): string {
	return `${BOLT_STORAGE_PREFIX}.${accountId}`;
}

/** Parse the stored ack list. Anything that isn't a JSON array of non-empty
 *  strings — absent, corrupt, wrong shape, a stray number — reads as
 *  "nothing taken" instead of throwing. */
export function readTakenBolts(raw: string | null | undefined): string[] {
	if (!raw) return [];
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return [];
	}
	if (!Array.isArray(parsed)) return [];
	return parsed.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

/** Serialize the ack list for storage, applying the FIFO cap on write so
 *  the stored value never grows past it regardless of how it was built. */
export function serializeTakenBolts(taken: string[]): string {
	return JSON.stringify(taken.slice(-BOLT_TAKEN_CAP));
}

/** Ack one run id. Idempotent in content — acking an id already in the
 *  store keeps one entry — and a re-take moves it to the front of the FIFO
 *  so it doesn't age out early just for being taken twice. */
export function takeBolt(taken: string[], runId: string): string[] {
	if (!runId) return taken;
	const next = taken.filter((id) => id !== runId);
	next.push(runId);
	return next.slice(-BOLT_TAKEN_CAP);
}

/** Ack every id in one write. */
export function takeAll(taken: string[], runIds: string[]): string[] {
	let next = taken;
	for (const runId of runIds) next = takeBolt(next, runId);
	return next;
}

/** The frame's `bolt:` value (data contract: `state.md` frontmatter gains
 *  `bolt: accepted <iso>` | `bolt: annotated <iso>`). Tolerant of anything
 *  a reader doesn't recognise — absent or unknown is "no bolt", never an
 *  error; the writer ships separately and old rows carry no field at all. */
export type BoltState = 'accepted' | 'annotated';

export function parseBoltState(value: string | null | undefined): BoltState | null {
	return value === 'accepted' || value === 'annotated' ? value : null;
}

/** One run carrying an unacked bolt, curated for the strip and the lane. */
export interface BoltRow {
	runId: string;
	/** The run's own name when it has one, else its id — never invented,
	 *  the same rule the cloth's own curated line follows. */
	name: string;
	named: boolean;
	bolt: BoltState;
	repoLabel: string | null;
	/** Epoch ms the run ended — what "newest first" sorts on. */
	endedAt: number;
	relics: RelicRecord[];
	/** The ledger's *measured* spend for this run — carried through for the
	 *  completion card's spend section (design-the-bolt.md §The completion
	 *  card). Nulls when the ledger row never carried them (a re-report
	 *  before the measurement lane populated, or a row that predates it) —
	 *  the card renders that as an honest absence, never a zero. */
	wallClockSeconds: number | null;
	tokensInput: number | null;
	tokensOutput: number | null;
	usdSubscriptionAttributed: number | null;
	usdCreditsEquivalent: number | null;
}

/**
 * A run "carries a bolt" = ended + a `bolt` field present. "Unacked" = it
 * carries a bolt and its run id is not in the viewer's ack store
 * (design-the-bolt.md §Data contract).
 *
 * One run can arrive as several ledger rows (re-reports); merged by run id
 * the same way the cloth itself merges (`cloth.ts`'s `mergeRuns`) — relics
 * accumulate, the latest close and bolt state win, the name fills in
 * first-known. Rows with no `run_id` are skipped: taking a bolt needs a
 * stable id to ack against, and a row without one can never leave the
 * strip.
 */
export function unackedBolts(
	rows: RunLedgerRow[],
	takenIds: ReadonlySet<string> | string[]
): BoltRow[] {
	const taken = takenIds instanceof Set ? takenIds : new Set(takenIds);
	const byId = new Map<string, BoltRow>();
	for (const row of rows) {
		const runId = row.run_id;
		if (!runId || taken.has(runId)) continue;
		const bolt = parseBoltState(row.bolt);
		if (!bolt) continue;
		const endedAt = row.ended_at ? Date.parse(row.ended_at) : Number.NaN;
		if (!Number.isFinite(endedAt)) continue;
		const name = row.name?.trim() || null;
		const existing = byId.get(runId);
		if (existing) {
			existing.endedAt = Math.max(existing.endedAt, endedAt);
			existing.bolt = bolt;
			existing.relics.push(...(row.external_refs ?? []));
			existing.repoLabel ??= row.repo_label;
			// Spend, like bolt state, is this re-report's own measurement — a
			// later row that actually carries a value wins, same as `bolt`
			// itself; a re-report that leaves a field null must not blank
			// out a figure an earlier row already measured.
			if (row.wall_clock_seconds !== null && row.wall_clock_seconds !== undefined) {
				existing.wallClockSeconds = row.wall_clock_seconds;
			}
			if (row.tokens_input !== null && row.tokens_input !== undefined) {
				existing.tokensInput = row.tokens_input;
			}
			if (row.tokens_output !== null && row.tokens_output !== undefined) {
				existing.tokensOutput = row.tokens_output;
			}
			if (
				row.usd_subscription_attributed !== null &&
				row.usd_subscription_attributed !== undefined
			) {
				existing.usdSubscriptionAttributed = row.usd_subscription_attributed;
			}
			if (row.usd_credits_equivalent !== null && row.usd_credits_equivalent !== undefined) {
				existing.usdCreditsEquivalent = row.usd_credits_equivalent;
			}
			if (!existing.named && name) {
				existing.name = name;
				existing.named = true;
			}
		} else {
			byId.set(runId, {
				runId,
				name: name ?? runId,
				named: name !== null,
				bolt,
				repoLabel: row.repo_label,
				endedAt,
				relics: [...(row.external_refs ?? [])],
				wallClockSeconds: row.wall_clock_seconds ?? null,
				tokensInput: row.tokens_input ?? null,
				tokensOutput: row.tokens_output ?? null,
				usdSubscriptionAttributed: row.usd_subscription_attributed ?? null,
				usdCreditsEquivalent: row.usd_credits_equivalent ?? null
			});
		}
	}
	return Array.from(byId.values()).sort((a, b) => b.endedAt - a.endedAt);
}

/** The strip's copy: `1 bolt awaits taking` singular, `N bolts await taking`
 *  otherwise — the only two shapes the strip ever renders. */
export function boltSummonsLabel(count: number): string {
	return count === 1 ? '1 bolt awaits taking' : `${count} bolts await taking`;
}

// ── The completion card (design-the-bolt.md §The completion card) ─────────
//
// Data honesty is the constraint the design doc names as this account's
// dominant defect class: render only what actually arrives on the wire, and
// say plainly when a section's data does not. The audit behind these two
// exports (see the report at the declared path): `cut_verb.py` parses a
// full declaration — `asks`, `decisions`, `owed`, a declared `spend`
// estimate, `next` — but `daemon.py`'s `_drain_outbox` only ever keeps
// `{accepted_at, annotated, spend_declared}` in `task.meta["bolt"]`, and
// both `state.md` (`_write_run_frame`) and `run_ledger.py`'s `_bolt_value`
// narrow that further to a bare `"accepted" | "annotated"` string. Nothing
// past that flag — not the mismatch text, not one `asks` row, not a single
// `owed` line, not the declared spend string — is written anywhere this
// frontend reads. Only two things the design's mockup describes actually
// reach the wire: the verdict flag itself, and produce (`external_refs`) +
// measured spend, which were already ledger columns before this feature.

/** The verdict head's label — `bolt.ts`'s two-value contract, worded for
 *  the card's first line. */
export function boltVerdictLabel(bolt: BoltState): string {
	return bolt === 'annotated' ? 'accepted — with dissent' : 'accepted';
}

/** Sections the completion card is prepared to render honestly, each tagged
 *  with whether real data reached the wire for it. `present` sections get
 *  the design's rendering; `absent` ones render as a labeled absence
 *  (design doc: "render a labeled absence... do not render empty-as-clean")
 *  rather than being skipped — a skipped section reads as "nothing to
 *  report" when the true state is "never carried here at all", which is
 *  exactly the distinction the constraint exists to keep visible.
 *
 *  `produce` and `spend` are conditioned on whether *this row* actually
 *  carries anything (an empty produce list or a null spend figure is a
 *  real, honest state, not a missing wire); `asks`, `decisions`, and `owed`
 *  are unconditionally absent — no code path persists them past the
 *  daemon's own validation pass, for any row, ever (see the module note
 *  above). Both are "absence", worded differently: a row with no relics
 *  says "this run made nothing"; `asks` says "this declaration was never
 *  carried past the daemon's own check". */
export interface BoltCardSections {
	asks: 'absent';
	decisions: 'absent';
	produce: 'present' | 'empty';
	owed: 'absent';
	spend: 'present' | 'empty';
}

/** The completion card's own data shape — the subset of `BoltRow` the card
 *  actually renders, decoupled from the strip/lane's ack-filtering. */
export interface BoltCardData {
	bolt: BoltState;
	relics: RelicRecord[];
	wallClockSeconds: number | null;
	tokensInput: number | null;
	tokensOutput: number | null;
	usdSubscriptionAttributed: number | null;
	usdCreditsEquivalent: number | null;
}

/** Merge every ledger row already known to belong to *one* run
 *  (`runNode.ts`'s `runLedgerRowsForNode`) into the card's data shape. Unlike
 *  `unackedBolts` (many runs, ack-filtered, keyed by id) this needs no key
 *  and no dedup — every row in the list is this run's own re-report history,
 *  latest-value-wins per field, same rule `unackedBolts` merges by. Null
 *  when no row carries a recognised bolt value: mirrors `parseBoltState`'s
 *  own contract that an absent/unrecognised value is "no bolt", never an
 *  error — the run node's "no `## Bolt` section" case, not a wire failure. */
export function boltCardDataFromLedgerRows(rows: RunLedgerRow[]): BoltCardData | null {
	let bolt: BoltState | null = null;
	const relics: RelicRecord[] = [];
	let wallClockSeconds: number | null = null;
	let tokensInput: number | null = null;
	let tokensOutput: number | null = null;
	let usdSubscriptionAttributed: number | null = null;
	let usdCreditsEquivalent: number | null = null;
	for (const row of rows) {
		const rowBolt = parseBoltState(row.bolt);
		if (rowBolt) bolt = rowBolt;
		relics.push(...(row.external_refs ?? []));
		if (row.wall_clock_seconds !== null && row.wall_clock_seconds !== undefined) {
			wallClockSeconds = row.wall_clock_seconds;
		}
		if (row.tokens_input !== null && row.tokens_input !== undefined) {
			tokensInput = row.tokens_input;
		}
		if (row.tokens_output !== null && row.tokens_output !== undefined) {
			tokensOutput = row.tokens_output;
		}
		if (row.usd_subscription_attributed !== null && row.usd_subscription_attributed !== undefined) {
			usdSubscriptionAttributed = row.usd_subscription_attributed;
		}
		if (row.usd_credits_equivalent !== null && row.usd_credits_equivalent !== undefined) {
			usdCreditsEquivalent = row.usd_credits_equivalent;
		}
	}
	if (!bolt) return null;
	return {
		bolt,
		relics,
		wallClockSeconds,
		tokensInput,
		tokensOutput,
		usdSubscriptionAttributed,
		usdCreditsEquivalent
	};
}

export function boltCardSections(row: {
	relics: RelicRecord[];
	wallClockSeconds: number | null;
	tokensInput: number | null;
	tokensOutput: number | null;
	usdSubscriptionAttributed: number | null;
	usdCreditsEquivalent: number | null;
}): BoltCardSections {
	const hasSpend =
		row.wallClockSeconds !== null ||
		row.tokensInput !== null ||
		row.tokensOutput !== null ||
		row.usdSubscriptionAttributed !== null ||
		row.usdCreditsEquivalent !== null;
	return {
		asks: 'absent',
		decisions: 'absent',
		produce: row.relics.length > 0 ? 'present' : 'empty',
		owed: 'absent',
		spend: hasSpend ? 'present' : 'empty'
	};
}
