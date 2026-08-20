import { parseRefs, type BackchannelRef } from './backchannelPage.ts';
import { runFace, runFacesInWindow, type RunFace } from './runFace.ts';
import type { SurfaceFile } from './surface.ts';

// The warp as a dependency graph (2026-08-11, the maintainer's dictated
// proposal, resolved in-thread): one item space, flat files, edges as rows.
//
// - An **item** is one authored markdown file `surface/warp/<id>.md` — a
//   decision (the user's), a preparation (mostly the user's), or an action
//   (mostly the resident's). `needs:` rows carry item ids: the dependency
//   edges. Multi-blocker is native — a list, not a tree.
// - A **topic** is one authored markdown file `surface/topics/<slug>.md` —
//   the filter axis (the Photoshop-layers idea), never a storage root. An
//   item wears topics as properties (`topics:` row); a topic carries an
//   alias id set (`ids:` row) so merges keep old links resolving.
// - **Everything else is derived, never authored**: blocked/ready fall out
//   of the edges (an edge to an undone item cannot lie the way a flag
//   nobody updates does), a run's topic set falls out of `taken:`/`done:`
//   rows, and the completed view is a filter, not a directory move —
//   storage never migrates by tense.
//
// This module supersedes `warp.ts` (layer files as storage roots): the
// inversion is the design — topics stopped being where items live and
// became properties items wear. Value imports carry `.ts` extensions
// because the tests run under node's own runner with no bundler in play.
//
// **Runs carry their topics; topics do not carry run lists** (maintainer,
// 2026-08-11, live: "I prefer the runs to have a list / set of topics it
// touches/touched"). The `taken:`/`done:` edges above answer for a run
// that lifted an item, but most runs (chat-woken, scheduled) take no
// item and so leave no edge at all — the retro-assignment door for those
// is a small file in the run's own node, `runs/<slug>/<run-id>/topics.md`
// (`isRunTopicsFile` / `parseRunTopics` below), carrying one `topics:` row
// in the same `splitIds` grammar the warp already speaks. `runTopicIndex`
// unions both doors; the corpus already mirrors the run-node directory
// these files live in (`runNode.ts`'s `runNodeFromSurface` reads
// `state.md`/`body.md`/`messages/` from the same prefix), so this is one
// more reader of a directory that already exists, not a new root.
//
// **The `goal` node kind** (2026-08-12, design-goal-oriented-engineering.md):
// same file grammar, one more legal `type:` value, allocated from its own
// `g-<N>` counter (daemon-side) so goal ids never collide with `w-<N>`. Any
// item — including a goal itself, for sub-goals — may carry `advances:`
// (the same list grammar as `needs:`, naming the goals it advances). A
// goal's *contributing cone* (`contributingCone`) and *blockers-on-you*
// (`blockersOnYou`) are derived, never authored — same rule as blocked/
// ready. Goals never fold into `readyItems`/`blockedItems`/`topicCounts`;
// `goalItems` is their own door. Mirrors `src/brr/items.py` in lockstep.

export const WARP_PREFIX = 'surface/warp/';
export const TOPICS_PREFIX = 'surface/topics/';

/** `goal` (design-goal-oriented-engineering.md, 2026-08-12) is the one node
 *  kind outside the original three — user-declared, its own `g-<N>` id
 *  space (daemon-side allocator), its own render band. It is otherwise the
 *  same row grammar; a goal never appears in the ready/held item bands
 *  (`readyItems`/`blockedItems` filter it out) — `goalItems` below is its
 *  door. */
export type ItemType = 'decision' | 'preparation' | 'action' | 'goal';

/** Lifecycle, fully derived — there is deliberately no `state:` row. A
 *  `done:` row makes an item done; a `retired:` row retires it; absence is
 *  open. One fact, one place: the receipt row *is* the state. */
export type ItemState = 'open' | 'done' | 'retired';

const KNOWN_TYPES: ReadonlySet<string> = new Set<ItemType>([
	'decision',
	'preparation',
	'action',
	'goal'
]);

/** Item ids are allocated (`w-42`, or `g-42` for a goal), never reused,
 *  rename-proof. The grammar is looser than the allocator on purpose: any
 *  slug-shaped basename parses, so a hand-authored id is an item, not a
 *  silent skip. */
const ID_RE = /^[a-z0-9][a-z0-9-]*$/;

export interface WarpItem {
	/** The file's basename without extension — the item's one address. */
	id: string;
	path: string;
	/** The `# ` title line's text; the id when the file has none. */
	headline: string;
	/** Null = no usable `type:` row — rendered as untyped, a drift finding,
	 *  never silently coerced into a guessed type. */
	type: ItemType | null;
	/** Topic ids as authored (aliases allowed — resolve via the graph). */
	topics: string[];
	/** Item ids this item depends on — the inbound edges of the graph. */
	needs: string[];
	/** Goal ids this item advances (same list grammar as `needs`). Legal on
	 *  any item, including a goal itself (a sub-goal edge) — see
	 *  `contributingCone` for what that case does and does not do. */
	advances: string[];
	state: ItemState;
	/** Run ids that took this item (daemon-written at ignition). */
	taken: string[];
	/** The completion receipt: `done: <YYYY-MM-DD> [run-…]`. */
	doneDate: string | null;
	doneRun: string | null;
	/** The retirement note: `retired: <YYYY-MM-DD> [free text]`. */
	retiredNote: string | null;
	refs: BackchannelRef[];
	prompt: string | null;
	/** Goal-only free-text rows (no parsing beyond the row grammar, per the
	 *  design). `null` when absent, on any item type — nothing here
	 *  enforces they only appear on a `goal`. */
	metric: string | null;
	target: string | null;
	horizon: string | null;
	bodyMarkdown: string;
}

export interface WarpTopic {
	/** The file's basename — the canonical id, and the rune seed. */
	canonicalId: string;
	/** Canonical id first, then `ids:` aliases — a merged topic keeps its
	 *  absorbed sibling's id here so old links keep resolving. */
	ids: string[];
	path: string;
	title: string;
	definitionMarkdown: string;
	/** Non-empty = this topic split; the file is a breadcrumb whose id is
	 *  retired (an id alive on two children would resolve to neither). */
	splitInto: string[];
}

export interface WarpGraph {
	items: WarpItem[];
	topics: WarpTopic[];
	itemById: Map<string, WarpItem>;
	/** Every id in every topic's alias set → the topic. */
	topicByAlias: Map<string, WarpTopic>;
}

const ROW_RE =
	/^(type|topics|needs|advances|done|retired|refs|prompt|taken|metric|target|horizon):[ \t]*(.*)$/;
const TITLE_RE = /^#[ \t]+(.*)$/;

function basename(path: string): string {
	const base = path.slice(path.lastIndexOf('/') + 1);
	return base.endsWith('.md') ? base.slice(0, -3) : base;
}

function splitIds(value: string): string[] {
	return value
		.split(/[\s·]+/)
		.map((part) => part.trim())
		.filter((part) => part.length > 0);
}

interface ParsedPage {
	title: string | null;
	rows: Map<string, string>;
	body: string;
}

/** One title line, one contiguous recognized-row block, then the body —
 *  the same open-below-the-schema grammar the backchannel taught: any
 *  unrecognized line ends the block and everything after is prose. */
function parsePage(markdown: string, rowRe: RegExp): ParsedPage {
	const lines = (markdown ?? '').replace(/\r\n/g, '\n').split('\n');
	let i = 0;
	let title: string | null = null;
	while (i < lines.length && lines[i].trim() === '') i += 1;
	const heading = i < lines.length ? TITLE_RE.exec(lines[i]) : null;
	if (heading) {
		title = heading[1].trim();
		i += 1;
	}
	while (i < lines.length && lines[i].trim() === '') i += 1;
	const rows = new Map<string, string>();
	while (i < lines.length) {
		const row = rowRe.exec(lines[i]);
		if (!row) break;
		// First row of a key wins; a repeated row is consumed (it was clearly
		// meant as schema) but never overrides.
		if (!rows.has(row[1])) rows.set(row[1], row[2].trim());
		i += 1;
	}
	const bodyLines = lines.slice(i);
	while (bodyLines.length && bodyLines[0].trim() === '') bodyLines.shift();
	while (bodyLines.length && bodyLines[bodyLines.length - 1].trim() === '') bodyLines.pop();
	return { title, rows, body: bodyLines.join('\n') };
}

export function isWarpItemFile(path: string): boolean {
	if (!path.startsWith(WARP_PREFIX)) return false;
	const rest = path.slice(WARP_PREFIX.length);
	if (!rest.endsWith('.md') || rest.includes('/')) return false;
	const id = rest.slice(0, -3);
	return id !== 'index' && ID_RE.test(id);
}

export function isTopicFile(path: string): boolean {
	if (!path.startsWith(TOPICS_PREFIX)) return false;
	const rest = path.slice(TOPICS_PREFIX.length);
	if (!rest.endsWith('.md') || rest.includes('/')) return false;
	const id = rest.slice(0, -3);
	return id !== 'index' && ID_RE.test(id);
}

export function parseWarpItem(path: string, markdown: string): WarpItem {
	const id = basename(path);
	const { title, rows, body } = parsePage(markdown, ROW_RE);
	const typeRaw = (rows.get('type') ?? '').toLowerCase();
	const doneRaw = rows.get('done') ?? null;
	const retiredRaw = rows.get('retired') ?? null;
	let doneDate: string | null = null;
	let doneRun: string | null = null;
	if (doneRaw !== null) {
		const parts = doneRaw.split(/\s+/).filter(Boolean);
		doneDate = parts[0] ?? null;
		doneRun = parts.find((part) => part.startsWith('run-')) ?? null;
	}
	const state: ItemState = doneRaw !== null ? 'done' : retiredRaw !== null ? 'retired' : 'open';
	return {
		id,
		path,
		headline: title ?? id,
		type: KNOWN_TYPES.has(typeRaw) ? (typeRaw as ItemType) : null,
		topics: splitIds(rows.get('topics') ?? ''),
		needs: splitIds(rows.get('needs') ?? ''),
		advances: splitIds(rows.get('advances') ?? ''),
		state,
		taken: splitIds(rows.get('taken') ?? ''),
		doneDate,
		doneRun,
		retiredNote: retiredRaw,
		refs: parseRefs(rows.get('refs') ?? ''),
		prompt: rows.get('prompt') || null,
		metric: rows.get('metric') || null,
		target: rows.get('target') || null,
		horizon: rows.get('horizon') || null,
		bodyMarkdown: body
	};
}

const TOPIC_ROW_RE = /^(ids|split-into)[:][ \t]*(.*)$/;

export function parseWarpTopic(path: string, markdown: string): WarpTopic {
	const canonicalId = basename(path);
	const { title, rows, body } = parsePage(markdown, TOPIC_ROW_RE);
	const aliases = splitIds(rows.get('ids') ?? '').filter((alias) => alias !== canonicalId);
	return {
		canonicalId,
		ids: [canonicalId, ...aliases],
		path,
		title: title ?? canonicalId,
		definitionMarkdown: body,
		splitInto: splitIds(rows.get('split-into') ?? '')
	};
}

/** Numeric-aware id order (`w-2` before `w-10`), stable for the whole UI. */
function compareIds(a: string, b: string): number {
	const na = /(\d+)$/.exec(a);
	const nb = /(\d+)$/.exec(b);
	if (na && nb && a.slice(0, -na[1].length) === b.slice(0, -nb[1].length)) {
		return Number(na[1]) - Number(nb[1]);
	}
	return a.localeCompare(b);
}

export function buildWarpGraph(files: SurfaceFile[]): WarpGraph {
	const items = files
		.filter((f) => isWarpItemFile(f.path))
		.map((f) => parseWarpItem(f.path, f.markdown))
		.sort((a, b) => compareIds(a.id, b.id));
	const topics = files
		.filter((f) => isTopicFile(f.path))
		.map((f) => parseWarpTopic(f.path, f.markdown))
		.sort((a, b) => a.canonicalId.localeCompare(b.canonicalId));
	const itemById = new Map(items.map((item) => [item.id, item]));
	const topicByAlias = new Map<string, WarpTopic>();
	for (const topic of topics) {
		for (const alias of topic.ids) {
			// First claim wins — two topics claiming one alias is a drift
			// finding for the audit, not a coin flip at render time.
			if (!topicByAlias.has(alias)) topicByAlias.set(alias, topic);
		}
	}
	return { items, topics, itemById, topicByAlias };
}

/** An item's topics, resolved through aliases to canonical topics, deduped
 *  in authored order. Unresolvable topic ids are dropped here — the drift
 *  audit, not the render, is where a dangling topic id gets named. */
export function resolveTopics(item: WarpItem, graph: WarpGraph): WarpTopic[] {
	const out: WarpTopic[] = [];
	for (const raw of item.topics) {
		const topic = graph.topicByAlias.get(raw);
		if (topic && !out.includes(topic)) out.push(topic);
	}
	return out;
}

export interface Blockers {
	/** Blocking edges: needed items that exist and are still open. */
	open: WarpItem[];
	/** Needed ids that resolve to no item — rendered as a warning, and
	 *  deliberately NOT blocking: a deleted blocker frees its dependents,
	 *  and the dangling chip (not a silent hold) is what catches the typo. */
	dangling: string[];
}

export function blockers(item: WarpItem, graph: WarpGraph): Blockers {
	const open: WarpItem[] = [];
	const dangling: string[] = [];
	for (const id of item.needs) {
		const needed = graph.itemById.get(id);
		if (!needed) dangling.push(id);
		else if (needed.state === 'open') open.push(needed);
	}
	return { open, dangling };
}

export function isBlocked(item: WarpItem, graph: WarpGraph): boolean {
	return blockers(item, graph).open.length > 0;
}

/** Open items that depend on this one — the "unblocks" direction. */
export function dependents(item: WarpItem, graph: WarpGraph): WarpItem[] {
	return graph.items.filter((other) => other.state === 'open' && other.needs.includes(item.id));
}

// `goal` sits in the Record for exhaustiveness only — a goal never reaches
// `typeRank` in practice, since `readyItems`/`blockedItems` both filter
// goals out before sorting (goals are a container, not a dispatchable/
// decidable item; see `goalItems` below for their own door).
const TYPE_ORDER: Record<ItemType, number> = { decision: 0, preparation: 1, action: 2, goal: 3 };

function typeRank(item: WarpItem): number {
	return item.type === null ? 3 : TYPE_ORDER[item.type];
}

function isGoal(item: WarpItem): boolean {
	return item.type === 'goal';
}

/** The ready band: open, unblocked, decisions first — the glance-decide-do
 *  order the surface exists for. Untyped items sink to the band's tail.
 *  Goals never fold in here — they render in their own section
 *  (`goalItems`). */
export function readyItems(graph: WarpGraph): WarpItem[] {
	return graph.items
		.filter((item) => item.state === 'open' && !isGoal(item) && !isBlocked(item, graph))
		.sort((a, b) => typeRank(a) - typeRank(b) || compareIds(a.id, b.id));
}

/** The held band: open but blocked, greyed below the ready band. Goals
 *  never fold in here either — see `readyItems`. */
export function blockedItems(graph: WarpGraph): WarpItem[] {
	return graph.items
		.filter((item) => item.state === 'open' && !isGoal(item) && isBlocked(item, graph))
		.sort((a, b) => typeRank(a) - typeRank(b) || compareIds(a.id, b.id));
}

/** Open goals, in id order — the warp's own band above the item lanes
 *  (design-goal-oriented-engineering.md). */
export function goalItems(graph: WarpGraph): WarpItem[] {
	return graph.items
		.filter((item) => item.state === 'open' && isGoal(item))
		.sort((a, b) => compareIds(a.id, b.id));
}

/** A goal's contributing cone, derived — never authored (the design's own
 *  rule): every item that directly `advances:` this goal id, plus the
 *  transitive `needs:` closure of those items. An item advancing a
 *  *different* goal that happens to be itself a sub-goal of this one is
 *  **not** pulled in — `advances:` on a goal is legal grammar (sub-goals)
 *  but nothing gives it special recursive treatment yet, mirroring
 *  `items.py`'s `contributing_cone` in lockstep. */
export function contributingCone(goalId: string, graph: WarpGraph): WarpItem[] {
	const coneIds = new Set<string>(
		graph.items.filter((item) => item.advances.includes(goalId)).map((item) => item.id)
	);
	const frontier = [...coneIds];
	while (frontier.length) {
		const current = graph.itemById.get(frontier.pop()!);
		if (!current) continue;
		for (const needed of current.needs) {
			if (graph.itemById.has(needed) && !coneIds.has(needed)) {
				coneIds.add(needed);
				frontier.push(needed);
			}
		}
	}
	return [...coneIds].map((id) => graph.itemById.get(id)!).sort((a, b) => compareIds(a.id, b.id));
}

/** The callback channel for one goal — a *query, not a list*: every open
 *  decision/preparation item inside its contributing cone. Mirrors
 *  `items.py`'s `blockers_on_you` in lockstep. */
export function blockersOnYou(goalId: string, graph: WarpGraph): WarpItem[] {
	return contributingCone(goalId, graph).filter(
		(item) => item.state === 'open' && (item.type === 'decision' || item.type === 'preparation')
	);
}

// ── goal readings: the trajectory table's data (design-goal-oriented-
// engineering.md §"a metrics block in the wake") ──────────────────────────
//
// A goal's readings store is a sibling file beside its item file —
// `surface/warp/g-<N>.readings.jsonl`, one JSON object per line, append-
// only. It rides the same authored-layer corpus mirror as the item file
// (`account.corpus_files` on the daemon side), so `/goals/[id]` finds it in
// `data.files` by path, exactly like it finds the goal's own `.md` file —
// no new endpoint. Mirrors `items.py`'s `Reading`/`load_readings`/
// `reading_summary` in lockstep.

export const READINGS_SUFFIX = '.readings.jsonl';

export interface GoalReading {
	ts: string;
	key: string;
	value: number;
	source: string;
	note: string | null;
	/** The measurement population a value was drawn from — a 5-item rolling
	 *  window vs a lifetime sum, whatever makes two same-`key` values
	 *  arithmetically incompatible. `null` when the writer didn't set one;
	 *  comparisons then fall back to `source` (see `readingBasis`), mirroring
	 *  `items.py`'s `Reading.basis` / `reading_basis`. */
	basis: string | null;
}

/** The value two readings must share for a Δ between them to be
 *  constructible. Explicit `basis` wins; absent one, `source` is the best
 *  available population signal. Mirrors `items.py`'s `reading_basis`. */
export function readingBasis(reading: GoalReading): string {
	return reading.basis ? reading.basis : reading.source;
}

/** The readings-file path for a goal id — never guessed at render time,
 *  always this one shape. */
export function goalReadingsPath(goalId: string): string {
	return `${WARP_PREFIX}${goalId}${READINGS_SUFFIX}`;
}

export function isGoalReadingsFile(path: string): boolean {
	if (!path.startsWith(WARP_PREFIX)) return false;
	const rest = path.slice(WARP_PREFIX.length);
	if (!rest.endsWith(READINGS_SUFFIX) || rest.includes('/')) return false;
	const id = rest.slice(0, -READINGS_SUFFIX.length);
	return /^g-\d+$/.test(id);
}

/** The corpus file carrying one goal's readings, or null when it never
 *  shipped one (an unread goal, or no readings-capable corpus mirror). */
export function findGoalReadingsFile(goalId: string, files: SurfaceFile[]): SurfaceFile | null {
	const path = goalReadingsPath(goalId);
	return files.find((f) => f.path === path) ?? null;
}

/** Every parseable sample, file order. A malformed line is skipped, not
 *  fatal — one bad line does not lose a goal's whole history, mirroring
 *  `items.py`'s `load_readings` tolerance. */
export function parseGoalReadings(markdown: string): GoalReading[] {
	const out: GoalReading[] = [];
	const withdrawn = new Set<string>();
	for (const rawLine of (markdown ?? '').split('\n')) {
		const line = rawLine.trim();
		if (!line) continue;
		let record: unknown;
		try {
			record = JSON.parse(line);
		} catch {
			continue;
		}
		if (typeof record !== 'object' || record === null) continue;
		const r = record as Record<string, unknown>;
		if (typeof r.ts !== 'string' || typeof r.key !== 'string') continue;
		if (r.withdrawn === true && typeof r.withdrawn_ts === 'string') {
			withdrawn.add(`${r.key}\0${r.withdrawn_ts}`);
			continue;
		}
		const value = typeof r.value === 'number' ? r.value : Number(r.value);
		if (!Number.isFinite(value)) continue;
		out.push({
			ts: r.ts,
			key: r.key,
			value,
			source: typeof r.source === 'string' ? r.source : '',
			note: typeof r.note === 'string' && r.note ? r.note : null,
			basis: typeof r.basis === 'string' && r.basis ? r.basis : null
		});
	}
	return out.filter((reading) => !withdrawn.has(`${reading.key}\0${reading.ts}`));
}

export interface GoalReadingSummary {
	latest: GoalReading;
	previous: GoalReading | null;
	delta: number | null;
	count: number;
	min: number;
	max: number;
	/** `latest`/`previous` exist but were drawn from different measurement
	 *  bases — the comparison was refused, not merely unavailable. Distinct
	 *  from the ordinary "no previous sample yet" case (`previous === null`),
	 *  which leaves this `false`. Mirrors `items.py`'s
	 *  `ReadingSummary.basis_mismatch`. */
	basisMismatch: boolean;
}

/** Per key: latest sample, previous sample, delta, sample count, min, max —
 *  chronological by `ts`, mirroring `items.py`'s `reading_summary`.
 *
 *  A Δ is only constructed when `latest` and `previous` share a measurement
 *  basis (`readingBasis`) — grouping by `key` alone put a lifetime sum and a
 *  rolling-window sum, both keyed `impressions`, into the same subtraction.
 *  A same-`key`, different-`basis` pair renders `delta: null` and
 *  `basisMismatch: true` instead of a number that looks real. */
/** Order two reading stamps that may differ in fractional-second width.
 *  Readings were whole-second until withdrawal handles needed to address one
 *  sample unambiguously, and are microsecond after. A raw string sort mixes
 *  the two wrongly — `.` sorts before `Z`, so a later microsecond sample
 *  sorts before an earlier whole-second one from the same second, and
 *  `latest` is what the goal surface publishes. Mirrors `items.py`'s
 *  `reading_ts_order_key`; the two must stay in lockstep. */
export function readingTsOrderKey(ts: string): string {
	const dot = ts.indexOf('.');
	if (dot === -1) return (ts.endsWith('Z') ? ts.slice(0, -1) : ts) + '.000000';
	const head = ts.slice(0, dot);
	const tail = ts.slice(dot + 1).replace(/Z$/, '');
	return head + '.' + (tail + '000000').slice(0, 6);
}

export function summarizeGoalReadings(readings: GoalReading[]): Map<string, GoalReadingSummary> {
	const byKey = new Map<string, GoalReading[]>();
	for (const reading of readings) {
		const bucket = byKey.get(reading.key) ?? [];
		bucket.push(reading);
		byKey.set(reading.key, bucket);
	}
	const out = new Map<string, GoalReadingSummary>();
	for (const [key, samples] of byKey) {
		const ordered = [...samples].sort((a, b) =>
			readingTsOrderKey(a.ts).localeCompare(readingTsOrderKey(b.ts))
		);
		const latest = ordered[ordered.length - 1];
		const previous = ordered.length > 1 ? ordered[ordered.length - 2] : null;
		const values = ordered.map((r) => r.value);
		const comparable = previous !== null && readingBasis(previous) === readingBasis(latest);
		out.set(key, {
			latest,
			previous,
			delta: comparable ? latest.value - (previous as GoalReading).value : null,
			count: ordered.length,
			min: Math.min(...values),
			max: Math.max(...values),
			basisMismatch: previous !== null && !comparable
		});
	}
	return out;
}

/** Newest-first render order for the trajectory table. */
export function readingsNewestFirst(readings: GoalReading[]): GoalReading[] {
	return [...readings].sort((a, b) => b.ts.localeCompare(a.ts));
}

/** Compact numeric rendering — integers stay bare, fractions trim trailing
 *  zeros. Mirrors `items.py`'s `format_value` so a number reads the same on
 *  the CLI and the dashboard. */
export function formatReadingValue(value: number): string {
	if (Number.isInteger(value)) return String(value);
	return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

/** Signed delta rendering — mirrors `items.py`'s `format_delta`. */
export function formatReadingDelta(value: number): string {
	const sign = value >= 0 ? '+' : '';
	return `${sign}${formatReadingValue(value)}`;
}

/** Done and retired items, newest receipt first — the completed tab. */
export function completedItems(graph: WarpGraph): WarpItem[] {
	return graph.items
		.filter((item) => item.state !== 'open')
		.sort((a, b) => {
			const da = a.doneDate ?? '';
			const db = b.doneDate ?? '';
			if (da !== db) return db.localeCompare(da);
			return compareIds(b.id, a.id);
		});
}

/** Topic filter: does this item stand in the lit set? An item with no
 *  resolvable topics passes only an all-lit filter (`selected === null`) —
 *  under a real filter, untagged is honestly "not this topic". */
export function itemInTopics(
	item: WarpItem,
	graph: WarpGraph,
	selected: ReadonlySet<string> | null
): boolean {
	if (selected === null) return true;
	return resolveTopics(item, graph).some((topic) => selected.has(topic.canonicalId));
}

// ── the run⇄topic join: runes transition from run ids to topic ids ────────

/** A run's own topic membership file: `runs/<slug>/<run-id>/topics.md`,
 *  a sibling of the `state.md` / `body.md` / `messages/` the corpus
 *  already mirrors under that prefix (`runNode.ts`). Four path segments,
 *  the last exactly `topics.md` — deliberately not a prefix match, so a
 *  message file or a future run-node kind never mis-parses as this one. */
export function isRunTopicsFile(path: string): boolean {
	const parts = path.split('/');
	return (
		parts.length === 4 &&
		parts[0] === 'runs' &&
		parts[1] !== '' &&
		parts[2] !== '' &&
		parts[3] === 'topics.md'
	);
}

/** The run id a `runs/<slug>/<run-id>/topics.md` path names, or null for
 *  any other path. */
export function runIdForTopicsPath(path: string): string | null {
	if (!isRunTopicsFile(path)) return null;
	return path.split('/')[2] ?? null;
}

const RUN_TOPICS_ROW_RE = /^(topics)[:][ \t]*(.*)$/;

/** The authored topic ids off a run's own `topics.md` — same `topics:`
 *  row grammar an item speaks, parsed with the module's shared row-block
 *  reader. Ids as authored (aliases allowed); resolution through
 *  `topicByAlias`, and dropping what doesn't resolve, is `runTopicIndex`'s
 *  job, the same second gate an item's `topics:` row goes through via
 *  `resolveTopics`. */
export function parseRunTopics(markdown: string): string[] {
	const { rows } = parsePage(markdown, RUN_TOPICS_ROW_RE);
	return splitIds(rows.get('topics') ?? '');
}

/** run id → canonical topic ids, via the items the run took or completed,
 *  unioned with each run's own `topics.md` claim (`isRunTopicsFile` /
 *  `parseRunTopics`) — the retro-assignment door for a run that took no
 *  item (chat-woken, scheduled) and so left no `taken:`/`done:` edge.
 *  This is the join the cloth's sigils and the topic filter read — a run
 *  wears the topics of the work it did, never a hue of its own.
 *  `files` defaults empty so a caller that hasn't wired the corpus feed
 *  through yet still gets the item-derived half, unchanged. */
export function runTopicIndex(
	graph: WarpGraph,
	files: readonly SurfaceFile[] = []
): Map<string, string[]> {
	const order = new Map(graph.topics.map((topic, index) => [topic.canonicalId, index]));
	const seen = new Map<string, Set<string>>();
	const add = (runId: string, item: WarpItem) => {
		if (!runId) return;
		const set = seen.get(runId) ?? new Set<string>();
		for (const topic of resolveTopics(item, graph)) set.add(topic.canonicalId);
		seen.set(runId, set);
	};
	for (const item of graph.items) {
		for (const runId of item.taken) add(runId, item);
		if (item.doneRun) add(item.doneRun, item);
	}
	for (const file of files) {
		const runId = runIdForTopicsPath(file.path);
		if (!runId) continue;
		const set = seen.get(runId) ?? new Set<string>();
		for (const raw of parseRunTopics(file.markdown)) {
			// An id the graph doesn't recognize is dropped silently here —
			// the drift audit's job to name, not this join's to guess at.
			const topic = graph.topicByAlias.get(raw);
			if (topic) set.add(topic.canonicalId);
		}
		seen.set(runId, set);
	}
	const index = new Map<string, string[]>();
	for (const [runId, set] of seen) {
		index.set(
			runId,
			[...set].sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
		);
	}
	return index;
}

/** The topic's face: same rune+hue derivation runs used to wear, now
 *  seeded from the *canonical topic id* — stable across merges (the id
 *  survives) and across topic-set changes (nothing is index-based). */
export function topicFace(topic: WarpTopic): RunFace {
	return runFace(topic.canonicalId);
}

/** The rune space — the Elder Futhark's 24 staves. The topic cap the
 *  maintainer set ("the amount of topics limited by the runes"): within
 *  ≤24 topics the collision probe below guarantees every topic a unique
 *  rune; past it, collisions are pigeonhole, and the rail says so. */
export const RUNE_SPACE = 24;

/** The perceptual floor a well-spread window still guarantees: below this
 *  many degrees apart, two hues read as "the same-ish color" at swatch
 *  size even with different runes next to them. `separateHues` below
 *  targets full even use of the circle (`360 / n`), which clears this
 *  floor for any n up to 12 — past that the floor itself becomes
 *  unreachable (13 topics can't sit ≥28° apart on one circle) and the
 *  pass falls back to the smaller even spacing that pigeonhole allows, the
 *  same "give ground gracefully" shape the glyph probe's alphabet-
 *  exhausted fallback uses. Exported for the tests below, not read by the
 *  computation itself — `separateHues` always asks for the whole circle;
 *  this is the floor that asking-for-the-whole-circle happens to clear. */
export const MIN_HUE_GAP = 28;

/** Hue-separation pass, same shape as the glyph collision probe above but on
 *  the color circle instead of the rune alphabet. A hash can and does put
 *  two topics within a few degrees of each other (his complaint: "6 topics
 *  can land neighbors"), and hue alone was the only thing telling them
 *  apart once the rune alphabet runs out — so this doesn't just clear
 *  collisions, it spreads the whole window across the full circle: sort by
 *  each topic's own hash-derived hue (ties broken by canonical id, so the
 *  sort — and everything downstream — is deterministic for a given topic
 *  set), then sweep forward asking for `360 / n` between consecutive hues,
 *  the widest even spacing n hues can share. A pair that already sat
 *  further apart than that keeps its distance; only a pair crowding each
 *  other gets pushed.
 *
 * The forward sweep can walk the last hue past a full turn; `overflow`
 * measures how far it and the seam back to the first hue overran the circle,
 * and the final loop compresses every hue by a fraction of that overflow
 * (more for later topics, none for the first) so the run closes without
 * disturbing sort order — the same "give ground gracefully" shape the glyph
 * probe's alphabet-exhausted fallback uses.
 *
 * Stability trade-off, stated once here per the borrowed-idiom rule: a
 * topic's hue can shift when the SET changes — a new topic landing between
 * two existing ones on the circle nudges every hue after it, exactly the
 * trade `runFacesInWindow`'s glyph probe already made for the rune. Only
 * hue and the derived `color` move; the glyph a topic already earned from
 * the probe above is untouched. Per-id hue stability across renames/set
 * changes was never the ask — same-page distinguishability was. */
function separateHues(faces: Map<string, RunFace>): Map<string, RunFace> {
	const n = faces.size;
	if (n <= 1) return faces;
	const order = [...faces.keys()].sort((a, b) => {
		const diff = faces.get(a)!.hue - faces.get(b)!.hue;
		return diff !== 0 ? diff : a.localeCompare(b);
	});
	const gap = 360 / n;
	const spread = [faces.get(order[0])!.hue];
	for (let i = 1; i < n; i += 1) {
		spread.push(Math.max(faces.get(order[i])!.hue, spread[i - 1] + gap));
	}
	const overflow = spread[n - 1] + gap - (spread[0] + 360);
	if (overflow > 0) {
		for (let i = 0; i < n; i += 1) spread[i] -= overflow * (i / (n - 1));
	}
	const out = new Map<string, RunFace>();
	order.forEach((id, i) => {
		const hue = Math.round(((spread[i] % 360) + 360) % 360);
		const face = faces.get(id)!;
		out.set(id, { ...face, hue, color: `hsl(${hue} 48% 64%)` });
	});
	return out;
}

/** One face per topic, collision-probed within the set (the same
 *  `runFacesInWindow` machinery the cloth used for runs): within the rune
 *  space, no two topics share a stave, and — the hue-separation pass this
 *  function adds on top — no two topics' hues sit closer than
 *  `MIN_HUE_GAP` either. Keyed on canonical ids in sorted order so the
 *  assignment is deterministic for a given topic set. All surfaces must
 *  read faces from this one map — a surface hashing its own face would
 *  disagree with the rail the moment a probe re-rolls one. */
export function topicFaces(graph: WarpGraph): Map<string, RunFace> {
	const ids = graph.topics
		.filter((topic) => topic.splitInto.length === 0)
		.map((topic) => topic.canonicalId);
	return separateHues(runFacesInWindow(ids));
}

/** The thread alphabet, in topic order — what every crossing strip and the
 *  heddle rail share. Color from the topic's own face, not from position:
 *  an index-based hue reshuffles the whole page when one topic retires. */
export interface TopicThread {
	canonicalId: string;
	title: string;
	face: RunFace;
}

export function topicThreads(graph: WarpGraph): TopicThread[] {
	const faces = topicFaces(graph);
	return graph.topics
		.filter((topic) => topic.splitInto.length === 0)
		.map((topic) => ({
			canonicalId: topic.canonicalId,
			title: topic.title,
			face: faces.get(topic.canonicalId) ?? topicFace(topic)
		}));
}

/** Items currently held by a live run — the visible frame the warp draws
 *  around work in flight (his rider: framed on the surface, never hidden). */
export function liveTakenRuns(item: WarpItem, liveRunIds: ReadonlySet<string>): string[] {
	return item.taken.filter((runId) => liveRunIds.has(runId));
}

/** An item weaving right now, in the pick lane's own vocabulary: the lane
 *  folds these onto the burning row as `⟶ headline topic` chips. */
export interface WeavingRow {
	/** The item's first canonical topic id — the chip's thread label.
	 *  Empty for an untagged item; the chip then carries the headline alone. */
	callSign: string;
	headline: string;
	itemId: string;
	liveRunId: string;
}

/** Open items whose `taken:` runs intersect the live set, in graph order. */
export function weavingRows(graph: WarpGraph, liveRunIds: ReadonlySet<string>): WeavingRow[] {
	const rows: WeavingRow[] = [];
	if (liveRunIds.size === 0) return rows;
	for (const item of graph.items) {
		if (item.state !== 'open') continue;
		const live = item.taken.find((runId) => liveRunIds.has(runId));
		if (!live) continue;
		const topics = resolveTopics(item, graph);
		rows.push({
			callSign: topics[0]?.canonicalId ?? '',
			headline: item.headline,
			itemId: item.id,
			liveRunId: live
		});
	}
	return rows;
}

/** Forge object hrefs that name a repo — issues, PRs, commits, trees,
 *  blobs. Conservative on purpose; `github.com/orgs/...` is not a repo
 *  coordinate and must not parse as one. (Carried over from `warp.ts` —
 *  an item's repo set stays a structural property of its refs, never a
 *  declared row, so a new repo joins the warp with no edit anywhere.) */
const FORGE_HREF_RE =
	/^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/(?:issues|pull|commit|tree|blob)\//;

const FORGE_LABEL_RE = /^([\w.-]+\/[\w.-]+)#\d+$/;

/** The repos an item's refs name, deduplicated, in first-mention order. */
export function itemRepos(item: WarpItem): string[] {
	const repos: string[] = [];
	const add = (repo: string) => {
		if (!repos.includes(repo)) repos.push(repo);
	};
	for (const ref of item.refs) {
		const label = FORGE_LABEL_RE.exec(ref.label.trim());
		if (label) add(label[1]);
		if (ref.href) {
			const href = FORGE_HREF_RE.exec(ref.href);
			if (href) add(href[1]);
		}
	}
	return repos;
}

/** Open-item counts per canonical topic id, split ready/blocked — the
 *  heddle rail's chips. Untagged items count under `''`. */
export interface TopicCounts {
	ready: number;
	blocked: number;
}

export function topicCounts(graph: WarpGraph): Map<string, TopicCounts> {
	const counts = new Map<string, TopicCounts>();
	const bump = (key: string, blocked: boolean) => {
		const entry = counts.get(key) ?? { ready: 0, blocked: 0 };
		if (blocked) entry.blocked += 1;
		else entry.ready += 1;
		counts.set(key, entry);
	};
	for (const item of graph.items) {
		// The heddle rail lenses the item lanes; goals carry their own
		// section and their own count elsewhere, never these chips.
		if (item.state !== 'open' || item.type === 'goal') continue;
		const blocked = isBlocked(item, graph);
		const topics = resolveTopics(item, graph);
		if (topics.length === 0) bump('', blocked);
		for (const topic of topics) bump(topic.canonicalId, blocked);
	}
	return counts;
}
