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
				relics: [...(row.external_refs ?? [])]
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
