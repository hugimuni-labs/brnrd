import type { RunLedgerRow } from './runLedger.ts';
import { rollupProduceGauge, type ProduceGaugeSummary } from './produceGauge.ts';
import { produceChips, selvageParts } from './cloth.ts';
import { runNodeHref } from './runNode.ts';
import { unackedBolts, type BoltRow } from './bolts.ts';
import type { ResolvedPathname } from '$app/types';

// THE DIGEST (design-run-route.md §The home page becomes a map, #1256 —
// signed 2026-08-09, "digest sounds cool"): replaces the bolt strip *and*
// the cloth-head lane (`Cloth.svelte`'s own copy of the same per-row
// TAKE/TAKE-ALL mechanism over the same `unackedBolts` array) — both were
// the exact "N per-run acceptance chips" the maintainer measured growing
// unchecked (23→37 in two hours) and called "a waste of time" after
// actually trying to use it. One aggregate line windowed to the viewer's
// own absence, plus only the rows that carry something addressed to the
// viewer. No per-row ack, no growing counter: glancing is the taking: at
// most one "caught up" press moves the anchor forward. Per-run bolt DATA
// is untouched — `bolts.ts`, `BoltCompletionCard`, and the run node's own
// `#receipt` section keep reading it exactly as before; this module only
// changes what summons attention at the door.

const DIGEST_STORAGE_PREFIX = 'brnrd.digest.lastLookedAt';

export function digestLastLookedStorageKey(accountId: string): string {
	return `${DIGEST_STORAGE_PREFIX}.${accountId}`;
}

/** First-ever-visit fallback span — the retired 24h block's own default —
 *  so a viewer with no recorded look yet gets a bounded, honestly-labeled
 *  digest rather than the account's entire history. Never used again once
 *  a viewer has pressed "caught up" at least once. */
export const DIGEST_FALLBACK_WINDOW_MS = 24 * 60 * 60 * 1000;

/** Parse the stored anchor. Anything that isn't a finite, positive,
 *  not-in-the-future epoch-ms instant reads as "never looked" — corrupt or
 *  absent storage must never fabricate a look that didn't happen (the
 *  optimistic-direction lie #1256 names explicitly). */
export function readLastLookedAt(raw: string | null | undefined, nowMs: number): number | null {
	if (!raw) return null;
	const parsed = Number(raw);
	return Number.isFinite(parsed) && parsed > 0 && parsed <= nowMs ? parsed : null;
}

export function serializeLastLookedAt(ms: number): string {
	return String(Math.trunc(ms));
}

/** The concrete instant the digest windows against: the viewer's own last
 *  confirmed look, or `now − DIGEST_FALLBACK_WINDOW_MS` when none is
 *  recorded yet. Always a real instant — rendered visibly as "since
 *  <timestamp>" (`runLedger.ts`'s shared `ageLabel` grammar), never a
 *  vague "recently". The anchor only ever advances on an explicit
 *  "caught up" press (see `+page.svelte`) — it must not creep forward just
 *  because the page rendered, or a viewer who glanced without reading
 *  loses the very thing the digest exists to hold for them. */
export function digestAnchor(lastLookedAt: number | null, nowMs: number): number {
	return lastLookedAt ?? nowMs - DIGEST_FALLBACK_WINDOW_MS;
}

/** "An ask disposition addressed to the viewer, dissent, produce worth
 *  announcing" — design-run-route.md §The summons split, the maintainer's
 *  own signed wording. The one predicate deciding whether a completed run
 *  interrupts, or folds silently into the cloth like every other receipt
 *  (every run still mints one — capability universal, obligation
 *  differential). `produceChips` is the cloth's own narrower produce
 *  vocabulary (pr/commit/kb) — deliberately excludes the `reply` relic
 *  every closed run's archived terminal reply creates, or almost every
 *  scheduled tick would summon on that alone. */
export function isSummonsWorthy(row: BoltRow): boolean {
	if (row.bolt === 'annotated') return true; // the daemon's own dissent
	const declaration = row.declaration;
	if (declaration && 'omitted' in declaration) return true; // declared, too big to store — surface it
	if (declaration && !('omitted' in declaration) && declaration.asks.length > 0) return true;
	return produceChips(row.relics).length > 0;
}

/** One summons-worthy row, curated for the digest. */
export interface DigestRow {
	runId: string;
	name: string;
	named: boolean;
	repoLabel: string | null;
	endedAt: number;
	bolt: BoltRow['bolt'];
	href: ResolvedPathname;
}

export interface Digest {
	/** The anchor this digest windowed against — echo of the caller's own
	 *  `since` argument, carried through so a render doesn't need a second
	 *  source for the instant it already asked for. */
	since: number;
	summary: ProduceGaugeSummary;
	/** `selvageParts`' compact rendering of `summary` — `"12 runs · 4h 32m ·
	 *  3 prs · …"`, the exact grammar the cloth's own hem already speaks. */
	summaryParts: string[];
	rows: DigestRow[];
}

/**
 * Build the digest: the aggregate over every row that closed since
 * `sinceMs`, plus the summons-worthy subset of bolt-carrying runs in the
 * same window. Reuses `unackedBolts` purely as a merge-by-run-id
 * projection (empty taken set — nothing pre-excluded); this module never
 * touches ack state.
 */
export function buildDigest(rows: RunLedgerRow[], sinceMs: number, nowMs: number): Digest {
	const windowMs = Math.max(0, nowMs - sinceMs);
	const summary = rollupProduceGauge(rows, nowMs, windowMs);
	const bolts = unackedBolts(rows, []);
	const digestRows: DigestRow[] = bolts
		.filter((row) => row.endedAt >= sinceMs && row.endedAt <= nowMs)
		.filter(isSummonsWorthy)
		.map((row) => ({
			runId: row.runId,
			name: row.name,
			named: row.named,
			repoLabel: row.repoLabel,
			endedAt: row.endedAt,
			bolt: row.bolt,
			// Every digest row is summons-worthy (`isSummonsWorthy`, above) —
			// it carries a bolt verdict, a declared ask, or produce worth
			// announcing, so the run's own `#receipt` section (RunNode.svelte)
			// always has something to land on, not just the bare node.
			href: (runNodeHref(row.repoLabel, row.runId) + '#receipt') as ResolvedPathname
		}));
	return { since: sinceMs, summary, summaryParts: selvageParts(summary), rows: digestRows };
}
