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

export const WARP_PREFIX = 'surface/warp/';
export const TOPICS_PREFIX = 'surface/topics/';

export type ItemType = 'decision' | 'preparation' | 'action';

/** Lifecycle, fully derived — there is deliberately no `state:` row. A
 *  `done:` row makes an item done; a `retired:` row retires it; absence is
 *  open. One fact, one place: the receipt row *is* the state. */
export type ItemState = 'open' | 'done' | 'retired';

const KNOWN_TYPES: ReadonlySet<string> = new Set<ItemType>(['decision', 'preparation', 'action']);

/** Item ids are allocated (`w-42`), never reused, rename-proof. The
 *  grammar is looser than the allocator on purpose: any slug-shaped
 *  basename parses, so a hand-authored id is an item, not a silent skip. */
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

const ROW_RE = /^(type|topics|needs|done|retired|refs|prompt|taken):[ \t]*(.*)$/;
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
		state,
		taken: splitIds(rows.get('taken') ?? ''),
		doneDate,
		doneRun,
		retiredNote: retiredRaw,
		refs: parseRefs(rows.get('refs') ?? ''),
		prompt: rows.get('prompt') || null,
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

const TYPE_ORDER: Record<ItemType, number> = { decision: 0, preparation: 1, action: 2 };

function typeRank(item: WarpItem): number {
	return item.type === null ? 3 : TYPE_ORDER[item.type];
}

/** The ready band: open, unblocked, decisions first — the glance-decide-do
 *  order the surface exists for. Untyped items sink to the band's tail. */
export function readyItems(graph: WarpGraph): WarpItem[] {
	return graph.items
		.filter((item) => item.state === 'open' && !isBlocked(item, graph))
		.sort((a, b) => typeRank(a) - typeRank(b) || compareIds(a.id, b.id));
}

/** The held band: open but blocked, greyed below the ready band. */
export function blockedItems(graph: WarpGraph): WarpItem[] {
	return graph.items
		.filter((item) => item.state === 'open' && isBlocked(item, graph))
		.sort((a, b) => typeRank(a) - typeRank(b) || compareIds(a.id, b.id));
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

/** run id → canonical topic ids, via the items the run took or completed.
 *  This is the join the cloth's sigils and the topic filter read — a run
 *  wears the topics of the work it did, never a hue of its own. */
export function runTopicIndex(graph: WarpGraph): Map<string, string[]> {
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
		if (item.state !== 'open') continue;
		const blocked = isBlocked(item, graph);
		const topics = resolveTopics(item, graph);
		if (topics.length === 0) bump('', blocked);
		for (const topic of topics) bump(topic.canonicalId, blocked);
	}
	return counts;
}
