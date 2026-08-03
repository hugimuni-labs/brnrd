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
import { loomBarFraction, loomPastStop, nestShelfChildren } from './loomBand.ts';
import { THERMAL_STOPS } from './statusPalette.ts';
import { rollupProduceGauge, type ProduceGaugeSummary } from './produceGauge.ts';
import { moodFace, type MoodFace } from './liveRuns.ts';

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
	/** True only when a resident authored the name — false when `name` is
	 * just the run id leaking through. Nameless roots fold per day. */
	named: boolean;
	repoLabel: string | null;
	/** The economical repo marker: null when the window is single-repo or
	 * this row rides the window's dominant repo; otherwise the short repo
	 * name (owner stripped) with the full label kept for title/hover.
	 * Derived from the window's own rows — no config. */
	repoChip: { short: string; full: string } | null;
	/** Warp item addresses from the run's `item` relics (THE WELD, #972):
	 * the standing intent this run was ignited from. Empty for un-welded runs. */
	items: string[];
	/** Runner identity from the ledger row (shell/core), for the in-place
	 * node unfold's header — first-known wins across re-reports. */
	runnerShell: string | null;
	runnerCore: string | null;
	chips: ClothChip[];
	/** A run that closed without produce still happened — faint line. */
	bare: boolean;
	duration: string;
	wallSeconds: number;
	age: string;
	ageMs: number;
	/** The close instant (epoch ms) — what the day rule groups on. */
	endedAt: number;
	/** The band's thermal-age color for this row — `THERMAL_STOPS` keyed by
	 * `loomPastStop(ageMs)`, the exact pair the shelf computes `run.color`
	 * from. Shared, never copied: the cloth and the band are one grammar at
	 * two zooms, so a hue drift between them would be a lie. */
	color: string;
	/** Bar width for the row, `loomBarFraction(wallSeconds, max)` where max
	 * is the window-wide maximum (`ClothWeave.maxWallSeconds`) — global, not
	 * per day, so bars compare across day rules. Carries the band's floor:
	 * a zero-second run is still visibly a bar, not a dot. */
	barFraction: number;
	/** THE FACE IN THREE TENSES piece 3: the run's final mood, the biography
	 * half of identity — resolved through the same `moodFace()` every live
	 * surface uses, straight off `RunLedgerRow`'s (currently always-absent)
	 * `mood*` fields. Null for the ordinary reason (no mood set) and for the
	 * standing one (the backend lane doesn't publish it yet, `runLedger.ts`'s
	 * own comment on those fields names the gap) — this row can't and
	 * doesn't tell the two apart, which is exactly why it renders nothing
	 * rather than guessing. */
	mood: MoodFace | null;
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
	/** The largest wall clock among the rendered lines (roots and workers)
	 * — the shared denominator every bar's fraction was computed against. */
	maxWallSeconds: number;
}

/** The per-day fold of nameless roots: one quiet line's worth of facts,
 * with the raw trees kept whole for expansion — folded, never dropped. */
export interface ClothUnnamedFold {
	count: number;
	totalSeconds: number;
	/** `4 unnamed ticks · 1m 36s total` — durations speak the ledger's
	 * shared grammar (`durationLabel`), same as every other cloth figure. */
	label: string;
	trees: ClothTree[];
}

/** One calendar day of the weave, under its own slim rule. */
export interface ClothDay {
	/** Stable key: the local date, `2026-08-01`. */
	key: string;
	/** The rule's quiet-caps text: `aug 1`. */
	dayLabel: string;
	/** Every root that closed this day, named and unnamed alike. */
	runCount: number;
	/** Named roots, newest first — the day's readable rows. */
	trees: ClothTree[];
	/** Nameless roots folded into one line; null when the day has none. */
	unnamed: ClothUnnamedFold | null;
}

function isKb(relic: RelicRecord): boolean {
	// `kb_page` is the same produce as `kb` — same alias the gauge and the
	// loom shelf both honour.
	return relic.kind === 'kb' || relic.kind === 'kb_page';
}

/** Produce chips in the loom shelf's legend grammar: `2pr 5c 1kb`. */
/** THE WELD (#972), the run's half of the back-pointer: the warp item
 * addresses (`layer#slug`) this run's manifest names — the item that ignited
 * it. Rendered as an ancestry chip on the cloth line; the item's own `taken:`
 * row points back. Referencing, never re-listing. */
export function itemAddresses(relics: RelicRecord[]): string[] {
	const out: string[] = [];
	for (const relic of relics) {
		if (relic.kind !== 'item') continue;
		const address = typeof relic.address === 'string' ? relic.address : '';
		if (address && !out.includes(address)) out.push(address);
	}
	return out;
}

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
	runnerShell: string | null;
	runnerCore: string | null;
	endedAt: number;
	wallSeconds: number;
	relics: RelicRecord[];
	ageMs: number;
	mood: string | null;
	moodGlyph: string | null;
	moodFrames: string[][] | null;
	moodRest: string | null;
	moodPitch: number | null;
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
			current.runnerShell ??= row.runner_shell;
			current.runnerCore ??= row.runner_core;
			current.endedAt = Math.max(current.endedAt, endedAt);
			current.wallSeconds = Math.max(current.wallSeconds, row.wall_clock_seconds ?? 0);
			current.relics.push(...(row.external_refs ?? []));
			// First-known wins, same rule runner identity follows above — a
			// mood set on one re-report shouldn't be clobbered by a later
			// report that (today) never carries one at all.
			current.mood ??= row.mood ?? null;
			current.moodGlyph ??= row.mood_glyph ?? null;
			current.moodFrames ??= row.mood_frames ?? null;
			current.moodRest ??= row.mood_rest ?? null;
			current.moodPitch ??= row.mood_pitch ?? null;
		} else {
			const entry: MergedRun = {
				id,
				runId: row.run_id,
				parentRunId: row.parent_run_id,
				isSubspawn: Boolean(row.is_subspawn),
				repoLabel: row.repo_label,
				name: row.name,
				runnerShell: row.runner_shell,
				runnerCore: row.runner_core,
				endedAt,
				wallSeconds: row.wall_clock_seconds ?? 0,
				relics: [...(row.external_refs ?? [])],
				mood: row.mood ?? null,
				moodGlyph: row.mood_glyph ?? null,
				moodFrames: row.mood_frames ?? null,
				moodRest: row.mood_rest ?? null,
				moodPitch: row.mood_pitch ?? null,
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
	const authoredName = run.name?.trim() || null;
	return {
		id: run.id,
		runId: run.runId,
		href: run.runId ? runNodeHref(run.repoLabel, run.runId) : null,
		name: authoredName || run.runId || 'run',
		named: authoredName !== null,
		repoLabel: run.repoLabel,
		repoChip: null,
		items: itemAddresses(run.relics),
		runnerShell: run.runnerShell,
		runnerCore: run.runnerCore,
		chips,
		bare: chips.length === 0,
		duration: durationLabel(run.wallSeconds),
		wallSeconds: run.wallSeconds,
		age: clothAgeLabel(run.ageMs),
		ageMs: run.ageMs,
		endedAt: run.endedAt,
		color: THERMAL_STOPS[loomPastStop(run.ageMs)],
		barFraction: 0,
		mood: moodFace(run.mood, run.moodGlyph, run.moodPitch, run.moodFrames, run.moodRest)
	};
}

/**
 * Bar lengths in the band's own scale: `loomBarFraction` (sqrt, floored)
 * against one window-wide maximum over every rendered line — roots and
 * workers alike, across all days — so a long bar on jul 30 and a long bar
 * on aug 1 mean the same thing. Runs the shelf's function, not a copy:
 * the floor that keeps a zero-second run visibly a bar comes with it.
 */
function dressBars(trees: ClothTree[]): number {
	const lines: ClothLine[] = [];
	for (const tree of trees) {
		lines.push(tree.root, ...tree.children);
	}
	const maxWallSeconds = lines.reduce((max, line) => Math.max(max, line.wallSeconds), 0);
	for (const line of lines) {
		line.barFraction = loomBarFraction(line.wallSeconds, maxWallSeconds);
	}
	return maxWallSeconds;
}

/**
 * Repo chip economy, derived from the window itself (no config): when one
 * repo covers every run, no row wears a label at all — the whole cloth is
 * that repo, so per-row repetition is pure noise. With several repos in the
 * window, only rows *off* the most common repo get a chip, and the chip is
 * the short repo name (owner stripped); the full label rides `full` for
 * title/hover. Ties on the count break toward the newest-first order the
 * weave already carries, so the derivation is deterministic.
 */
function dressRepoChips(trees: ClothTree[]): void {
	const lines: ClothLine[] = [];
	for (const tree of trees) {
		lines.push(tree.root, ...tree.children);
	}
	const counts = new Map<string, number>();
	for (const line of lines) {
		if (line.repoLabel) counts.set(line.repoLabel, (counts.get(line.repoLabel) ?? 0) + 1);
	}
	if (counts.size <= 1) return;
	let dominant: string | null = null;
	let best = 0;
	for (const [label, count] of counts) {
		if (count > best) {
			dominant = label;
			best = count;
		}
	}
	for (const line of lines) {
		if (line.repoLabel && line.repoLabel !== dominant) {
			line.repoChip = {
				short: line.repoLabel.split('/').pop() || line.repoLabel,
				full: line.repoLabel
			};
		}
	}
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
	dressRepoChips(kept);
	const maxWallSeconds = dressBars(kept);
	return { trees: kept, dropped: trees.length - kept.length, maxWallSeconds };
}

const DAY_MONTHS = [
	'jan',
	'feb',
	'mar',
	'apr',
	'may',
	'jun',
	'jul',
	'aug',
	'sep',
	'oct',
	'nov',
	'dec'
] as const;

/**
 * Group a weave's trees by the *local* calendar day each root closed —
 * the same clock `runLedger`'s "today" check already reads (`getMonth`/
 * `getDate` on the local Date), so the rule that says `aug 1` agrees with
 * the wall clock of whoever is looking at the page. Trees arrive newest
 * first, so days come out newest first; within a day, named roots keep
 * that order and nameless roots (`named === false` — the title would just
 * be the run id) fold into one `ClothUnnamedFold` per day: count, summed
 * wall clock, and the raw trees kept whole for expansion. A named run
 * never folds; a fold is never dropped — `runCount` counts both.
 */
export function groupClothDays(trees: ClothTree[]): ClothDay[] {
	const days: ClothDay[] = [];
	const byKey = new Map<string, ClothDay>();
	const pad = (value: number) => String(value).padStart(2, '0');
	for (const tree of trees) {
		const closed = new Date(tree.root.endedAt);
		const key = `${closed.getFullYear()}-${pad(closed.getMonth() + 1)}-${pad(closed.getDate())}`;
		let day = byKey.get(key);
		if (!day) {
			day = {
				key,
				dayLabel: `${DAY_MONTHS[closed.getMonth()]} ${closed.getDate()}`,
				runCount: 0,
				trees: [],
				unnamed: null
			};
			byKey.set(key, day);
			days.push(day);
		}
		day.runCount += 1;
		if (tree.root.named) {
			day.trees.push(tree);
		} else {
			day.unnamed ??= { count: 0, totalSeconds: 0, label: '', trees: [] };
			day.unnamed.count += 1;
			day.unnamed.totalSeconds += tree.root.wallSeconds;
			day.unnamed.trees.push(tree);
		}
	}
	for (const day of days) {
		if (day.unnamed) {
			const { count, totalSeconds } = day.unnamed;
			day.unnamed.label = `${count} unnamed tick${count === 1 ? '' : 's'} · ${durationLabel(totalSeconds)} total`;
		}
	}
	return days;
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
