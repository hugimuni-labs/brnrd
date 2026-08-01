// The cloth — the dashboard's past band, v1 (design-work-layers.md).
//
// The "last 24h" instruments section dies; the past renders as a sliding
// window (default 30 days) of done work: runs as root nodes of collapsed
// trees (run → worker subruns), one curated line each, expansion on demand.
// The selvage is the cloth's self-finished edge — one compact row of
// spend→produce aggregates over the same window.
//
// Pure helpers only: the component (`Cloth.svelte`) renders what these
// return, and the page passes rows in. No fetches, no new endpoints.
//
// Value imports carry `.ts` extensions because `cloth.test.ts` runs under
// node's own runner with no bundler in the loop — same rule `transitions.ts`
// documents for its `./boot.ts` import.

import type { ResolvedPathname } from '$app/types';
import { durationLabel, type RelicRecord, type RunLedgerRow } from './runLedger.ts';
import { runNodeHref } from './runNode.ts';
import { nestShelfChildren } from './loomBand.ts';
import { rollupProduceGauge, type ProduceGaugeSummary } from './produceGauge.ts';

/** The cloth's default sliding window: 30 days of done work. */
export const CLOTH_WINDOW_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * How many root runs the cloth renders before folding the rest into an
 * explicit "+ N older in the window" line. The cap bounds the DOM, not the
 * truth: `ClothWeave.dropped` is part of the return value precisely so the
 * component *must* have the drop count in hand — silent truncation is the
 * forbidden failure mode.
 */
export const CLOTH_ROOT_CAP = 40;

/** One produce chip on a curated line: `2pr`, `5c`, `1kb` — the same
 * compact legend grammar the loom shelf speaks. */
export interface ClothChip {
	kind: 'pr' | 'commit' | 'kb';
	count: number;
	label: string;
}

/** One curated line: everything the cloth says about a run when collapsed. */
export interface ClothLine {
	/** Stable render key: run_id, else event_id, else the close timestamp. */
	id: string;
	runId: string | null;
	/** Route to the run's Wyrd node; null when the row has no durable run_id
	 * (a href that can never resolve is worse than no link). */
	href: ResolvedPathname | null;
	/** The run's own name when it has one, else its id — never invented. */
	name: string;
	repoLabel: string | null;
	chips: ClothChip[];
	/** A run that closed without produce still happened — faint line. */
	bare: boolean;
	duration: string;
	wallSeconds: number;
	age: string;
	ageMs: number;
}

/** A root run with its worker subruns collapsed beneath it. */
export interface ClothTree {
	root: ClothLine;
	children: ClothLine[];
}

export interface ClothWeave {
	trees: ClothTree[];
	/** Root runs beyond the cap — rendered as "+ N older in the window". */
	dropped: number;
}

function isKb(relic: RelicRecord): boolean {
	// `kb_page` is the same produce as `kb` — same alias the gauge and the
	// loom shelf both honour.
	return relic.kind === 'kb' || relic.kind === 'kb_page';
}

/** Produce chips in the loom shelf's legend grammar: `2pr 5c 1kb`. */
export function produceChips(relics: RelicRecord[]): ClothChip[] {
	const prs = relics.filter((relic) => relic.kind === 'pr').length;
	const commits = relics.filter((relic) => relic.kind === 'commit').length;
	const kb = relics.filter(isKb).length;
	const chips: ClothChip[] = [];
	if (prs > 0) chips.push({ kind: 'pr', count: prs, label: `${prs}pr` });
	if (commits > 0) chips.push({ kind: 'commit', count: commits, label: `${commits}c` });
	if (kb > 0) chips.push({ kind: 'kb', count: kb, label: `${kb}kb` });
	return chips;
}

/** Same grammar the loom band's tooltips speak — m, then h m, then d h. */
export function clothAgeLabel(ms: number): string {
	const minutes = Math.max(0, Math.round(ms / 60_000));
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `${hours}h ${minutes % 60}m ago`;
	return `${Math.floor(hours / 24)}d ${hours % 24}h ago`;
}

/** The window predicate: a row belongs to the cloth when it *closed* inside
 * the trailing window. Unparseable or future timestamps stay out. */
export function inClothWindow(row: RunLedgerRow, now: number, windowMs: number): boolean {
	const endedAt = row.ended_at ? Date.parse(row.ended_at) : Number.NaN;
	const ageMs = now - endedAt;
	return Number.isFinite(endedAt) && ageMs >= 0 && ageMs <= windowMs;
}

// One run can surface as several ledger rows (re-reports). Merged by id the
// same way the loom shelf merges them: relics accumulate, the latest close
// and the largest wall clock win, identity fields fill in first-known.
interface MergedRun {
	id: string;
	runId: string | null;
	parentRunId: string | null;
	isSubspawn: boolean;
	repoLabel: string | null;
	name: string | null;
	endedAt: number;
	wallSeconds: number;
	relics: RelicRecord[];
	ageMs: number;
}

function mergeRuns(rows: RunLedgerRow[], now: number, windowMs: number): MergedRun[] {
	const merged: MergedRun[] = [];
	const byId = new Map<string, MergedRun>();
	for (const row of rows) {
		if (!inClothWindow(row, now, windowMs)) continue;
		const endedAt = Date.parse(row.ended_at ?? '');
		const id = row.run_id ?? row.event_id ?? row.ended_at ?? '';
		if (!id) continue;
		const current = byId.get(id);
		if (current) {
			current.runId ??= row.run_id;
			current.parentRunId ??= row.parent_run_id;
			current.isSubspawn ||= Boolean(row.is_subspawn);
			current.repoLabel ??= row.repo_label;
			current.name ??= row.name;
			current.endedAt = Math.max(current.endedAt, endedAt);
			current.wallSeconds = Math.max(current.wallSeconds, row.wall_clock_seconds ?? 0);
			current.relics.push(...(row.external_refs ?? []));
		} else {
			const entry: MergedRun = {
				id,
				runId: row.run_id,
				parentRunId: row.parent_run_id,
				isSubspawn: Boolean(row.is_subspawn),
				repoLabel: row.repo_label,
				name: row.name,
				endedAt,
				wallSeconds: row.wall_clock_seconds ?? 0,
				relics: [...(row.external_refs ?? [])],
				ageMs: 0
			};
			byId.set(id, entry);
			merged.push(entry);
		}
	}
	for (const entry of merged) entry.ageMs = now - entry.endedAt;
	return merged;
}

function curatedLine(run: MergedRun): ClothLine {
	const chips = produceChips(run.relics);
	return {
		id: run.id,
		runId: run.runId,
		href: run.runId ? runNodeHref(run.repoLabel, run.runId) : null,
		name: run.name?.trim() || run.runId || 'run',
		repoLabel: run.repoLabel,
		chips,
		bare: chips.length === 0,
		duration: durationLabel(run.wallSeconds),
		wallSeconds: run.wallSeconds,
		age: clothAgeLabel(run.ageMs),
		ageMs: run.ageMs
	};
}

/**
 * Weave the window's rows into root-run trees, newest root first, each
 * root's worker subruns age-ordered beneath it.
 *
 * The dispatch edge (`is_subspawn`/`parent_run_id`) is read through
 * `nestShelfChildren` — the loom shelf's own resolution of the same join,
 * with its orphan rule intact: a child whose parent is not in the window
 * renders as a root rather than silently vanishing.
 *
 * The cap applies to *roots* — a fleet's workers ride their root, they are
 * not what the reader scrolls past — and the overflow comes back as
 * `dropped`, which the component is obligated to render.
 */
export function weaveCloth(
	rows: RunLedgerRow[],
	now: number,
	windowMs: number = CLOTH_WINDOW_MS,
	cap: number = CLOTH_ROOT_CAP
): ClothWeave {
	const nested = nestShelfChildren(mergeRuns(rows, now, windowMs));
	const trees: ClothTree[] = [];
	for (const run of nested) {
		if (run.depth === 0) {
			trees.push({ root: curatedLine(run), children: [] });
		} else if (trees.length > 0) {
			trees[trees.length - 1].children.push(curatedLine(run));
		}
	}
	const kept = trees.slice(0, Math.max(0, cap));
	return { trees: kept, dropped: trees.length - kept.length };
}

/**
 * The selvage: spend→produce aggregates over the window's rows, straight
 * from the gauge's existing roll-up grammar (`rollupProduceGauge`) — PR
 * counts dedupe on repo#number, `kb_page` folds into kb, absent metrics
 * stay null rather than becoming fabricated zeroes.
 */
export function clothSelvage(
	rows: RunLedgerRow[],
	now: number,
	windowMs: number = CLOTH_WINDOW_MS
): ProduceGaugeSummary {
	return rollupProduceGauge(rows, now, windowMs);
}

/**
 * The selvage rendered as compact parts: `12 runs · 4h 32m · 3 prs · …`.
 * Runs always speak; wall clock speaks when the rows carried it; produce
 * kinds speak only when nonzero — a row of zeroes is noise, not an edge.
 */
export function selvageParts(summary: ProduceGaugeSummary): string[] {
	const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`;
	const parts = [plural(summary.runCount, 'run')];
	if (summary.wallClockSeconds !== null) parts.push(durationLabel(summary.wallClockSeconds));
	if (summary.prs > 0) parts.push(plural(summary.prs, 'pr'));
	if (summary.mergedPrs > 0) parts.push(`${summary.mergedPrs} merged`);
	if (summary.commits > 0) parts.push(plural(summary.commits, 'commit'));
	if (summary.kbPages > 0) parts.push(plural(summary.kbPages, 'kb page'));
	if (summary.replies > 0)
		parts.push(summary.replies === 1 ? '1 reply' : `${summary.replies} replies`);
	return parts;
}
