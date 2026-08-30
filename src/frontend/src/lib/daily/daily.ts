import { edgeLine, liveRunDisplayName, runCourse, type LiveRun } from '../liveRuns.ts';
import type { RelicRecord, RunLedgerRow } from '../runLedger.ts';
import {
	isBlocked,
	readyItems,
	resolveTopics,
	topicFaces,
	type WarpGraph,
	type WarpItem
} from '../warpGraph.ts';

export interface DailyLiveBar {
	run: LiveRun;
	name: string;
	act: string | null;
	course: string | null;
	pending: number;
	depth: number;
}

/** Parent-first presence rows. Children whose parent has already left stay visible at root. */
export function dailyLiveBars(runs: LiveRun[]): DailyLiveBar[] {
	const children = new Map<string, LiveRun[]>();
	const ids = new Set(runs.map((run) => run.run_id || run.id));
	for (const run of runs) {
		if (!run.parent_run_id || !ids.has(run.parent_run_id)) continue;
		const rows = children.get(run.parent_run_id) ?? [];
		rows.push(run);
		children.set(run.parent_run_id, rows);
	}
	const roots = runs.filter((run) => !run.parent_run_id || !ids.has(run.parent_run_id));
	const out: DailyLiveBar[] = [];
	const add = (run: LiveRun, depth: number) => {
		const course = runCourse(run.card_text, run.course);
		out.push({
			run,
			name: liveRunDisplayName(run),
			act: edgeLine(run.edge),
			course: course ? `${course.done}/${course.total}` : null,
			pending: run.portals?.pending ?? 0,
			depth
		});
		for (const child of children.get(run.run_id || run.id) ?? []) add(child, depth + 1);
	};
	for (const root of roots) add(root, 0);
	return out;
}

export interface DailyBuoy {
	item: WarpItem;
	mark: string;
	color: string;
	topic: string | null;
}

/** The surface claim is exactly open ∧ unblocked; goals have their own future band. */
export function dailyBuoys(graph: WarpGraph): DailyBuoy[] {
	const faces = topicFaces(graph);
	return readyItems(graph).map((item) => {
		const topic = resolveTopics(item, graph)[0] ?? null;
		return {
			item,
			mark: item.type === 'action' ? '♦' : '◇',
			color: (topic && faces.get(topic.canonicalId)?.color) || '#d9a441',
			topic: topic?.title ?? null
		};
	});
}

export interface DailyIsland {
	repo: string;
	branches: { name: string; pr: number | null; live: boolean }[];
}

function relicBranch(refs: RelicRecord[] | null): { name: string; pr: number | null } | null {
	const branch = refs?.find((ref) => ref.kind === 'branch');
	if (!branch?.name) return null;
	const pr = refs?.find((ref) => ref.kind === 'pr');
	return { name: String(branch.name), pr: typeof pr?.number === 'number' ? pr.number : null };
}

/** Only branch and PR facts present on the browser wire become terrain. */
export function dailyIslands(runs: LiveRun[], rows: RunLedgerRow[]): DailyIsland[] {
	const byRepo = new Map<string, Map<string, { name: string; pr: number | null; live: boolean }>>();
	const add = (
		repo: string | null | undefined,
		branch: string,
		pr: number | null,
		live: boolean
	) => {
		const label = repo || 'unknown project';
		const branches = byRepo.get(label) ?? new Map();
		const prior = branches.get(branch);
		branches.set(branch, {
			name: branch,
			pr: pr ?? prior?.pr ?? null,
			live: live || prior?.live || false
		});
		byRepo.set(label, branches);
	};
	for (const run of runs) if (run.room?.branch) add(run.repo_label, run.room.branch, null, true);
	for (const row of rows) {
		const branch = relicBranch(row.external_refs);
		if (branch) add(row.repo_label, branch.name, branch.pr, false);
	}
	return [...byRepo.entries()].map(([repo, branches]) => ({
		repo,
		branches: [...branches.values()]
	}));
}

export function knowledgePageCount(files: { layer?: string }[]): number | null {
	const served = files.some((file) => file.layer === 'knowledge');
	return served ? files.filter((file) => file.layer === 'knowledge').length : null;
}

export interface SurfaceBuoyField {
	shown: DailyBuoy[];
	hidden: number;
}

/**
 * The strip stays a line, not a wall. The live warp serves ~40 ready items;
 * rendering them all re-creates the `/` wall with color. Needs-you calls
 * (decisions/preparations) surface first, then dispatchable actions, capped —
 * and the remainder is counted, never vanished (the heddle rail's own rule).
 */
export function surfaceBuoys(buoys: DailyBuoy[], cap = 10): SurfaceBuoyField {
	const calls = buoys.filter((buoy) => buoy.item.type !== 'action');
	const actions = buoys.filter((buoy) => buoy.item.type === 'action');
	const ordered = [...calls, ...actions];
	return { shown: ordered.slice(0, cap), hidden: Math.max(0, ordered.length - cap) };
}

/** The buoy hash grammar: `#<item-id>`, trimmed and de-hashed — the one parse
 *  a cold `/daily#w-47` load and a live buoy press both resolve against, so
 *  they can't drift into two different notions of "what does this hash
 *  mean." Blank (`''`, `'#'`) reads as no selection rather than an id. */
export function hashItemId(hash: string): string | null {
	const id = hash.replace(/^#/, '').trim();
	return id.length > 0 ? id : null;
}

export type DailyItemState = 'ready' | 'blocked' | 'taken' | 'done' | 'retired';

/** The lifecycle the item detail panel reports, layered over the graph's
 *  plain open/done/retired: blocked and taken can both be true of an *open*
 *  item, so this picks the one that answers "what do I do about it" —
 *  blocked (something else has to move first) outranks taken (a run already
 *  claimed it) outranks plain ready. */
export function dailyItemState(item: WarpItem, graph: WarpGraph): DailyItemState {
	if (item.state === 'done') return 'done';
	if (item.state === 'retired') return 'retired';
	if (isBlocked(item, graph)) return 'blocked';
	if (item.taken.length > 0) return 'taken';
	return 'ready';
}
