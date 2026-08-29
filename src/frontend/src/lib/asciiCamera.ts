// The ASCII camera — one window into the unbounded room (#1652 slice 2).
// Replaces the packed-Block board: the world is laid out once (roomLayout),
// the camera rasterizes the part of it in frame. Resizing or panning moves
// the window, never a node.
//
//   - consumes RoomLayout coordinates; assigns no semantic location itself;
//   - deterministic: same topology + layout + camera + `now` ⇒ same bytes;
//   - off-camera activity renders as edge bearings, not shrunken terrain;
//   - CHARTS and the Cloth selvage stay rows below the board on purpose —
//     intent and cost are control state, not terrain.

import { fileFromDetail, type RoomActor, type RoomGraph, type ClothRow } from './roomGraph.ts';
import { terminalBox, type TerminalLine } from './roomTerminal.ts';
import {
	campId,
	type PlaceId,
	type PlaceNode,
	type PlaceNodeKind,
	type RoomTopology
} from './roomTopology.ts';
import { MAX_DIR_LABEL_CHARS, type Point, type RoomLayout } from './roomLayout.ts';
import type { PagerPage } from './roomPager.ts';
import { untilText } from './scheduledWakes.ts';
import { OFF_MARK } from './stateChrome.ts';
import type { CrossingFrame } from './roomCrossing.ts';

export type CameraLevel = 'island' | 'atlas';

export interface Camera {
	/** World-unit center of the window. */
	center: Point;
	cols: number;
	rows: number;
	level: CameraLevel;
}

export interface WorldRenderOpts {
	/** Wall clock for elapsed labels; omit ⇒ no elapsed labels (the
	 *  clock-free render the flash diff runs on). */
	now?: number;
	/** Place route to mark on the ground — the current transition. */
	highlightRoute?: PlaceId[] | null;
	/** Display positions for actors mid-walk (world units) — presentation
	 *  state derived from an attested BoundaryTransition, never a place. */
	actorPositions?: Record<string, Point> | null;
	/** Cap on cut Cloth rows below the board. */
	clothRows?: number;
	/** The accumulated pager feed, newest first — state, so it rides the
	 *  clock-free render and a fresh page flashes. */
	pages?: PagerPage[] | null;
	/** Reading-ceremony phase per actor run id (ticksLeft) — presentation,
	 *  like walk positions: passed on display paints, never the flash diff. */
	reading?: Record<string, number> | null;
	/** THE CROSSING, mid-ceremony: the claw's current extent and the letter
	 *  it carries. Presentation, like walk positions — the frames are derived
	 *  from an attested crossing (`RoomGraph.crossings`) and the caller owns
	 *  the clock, so this never rides the clock-free flash diff. */
	crossings?: CrossingFrame[] | null;
	/** THE TERMINAL's contents, newest first — the commands this run has run.
	 *  Accumulated by the caller across polls (`roomTerminal.recordCommands`),
	 *  like the trail and the pager, because the wire carries one cursor and
	 *  a crossing tail, never a run's whole labour. */
	terminal?: TerminalLine[] | null;
}

/** The resident's own state, in the pager's own grid: what it is burning,
 *  how soon that comes back, and how much body is free. Null when the wire
 *  has attested none of it — an empty strip beats a row of zeroes, which
 *  would read as measured. */
function conditionLine(graph: RoomGraph, now: number | undefined): string | null {
	const bits: string[] = [];
	for (const fuel of graph.garage) {
		const window = fuel.windows[0];
		if (!window || window.percent === null) continue;
		const until = fuel.resetShort ? ` ↻${fuel.resetShort}` : '';
		bits.push(`⛁ ${fuel.shell} ${window.label} ${Math.round(window.percent)}%${until}`);
	}
	const { active, max } = graph.slots;
	// `max` null means no daemon has reported a pool width. `active/?` says
	// that; `active/0` would be a claim nobody made.
	if (max !== null || active > 0) bits.push(`◈ ${active}/${max ?? '?'} slots`);
	if (graph.pendingLetters > 0) bits.push(`◇×${graph.pendingLetters} unread`);
	void now;
	return bits.length > 0 ? `  ⌁ ${bits.join('  ·  ')}` : null;
}

function garageReadings(graph: RoomGraph): string[] {
	return graph.garage.map((fuel) => {
		const off = fuel.status === 'known' ? '' : OFF_MARK;
		const window = fuel.windows[0];
		const figure =
			window?.percent === null || window?.percent === undefined
				? '?'
				: `${Math.round(window.percent)}%`;
		return `${off}${fuel.shell}${window ? ` ${window.label} ${figure}` : ''}`;
	});
}

// chars per world unit: island scale is the readable default; atlas
// compresses the same coordinates — never a different geography.
const SCALE: Record<CameraLevel, { x: number; y: number }> = {
	island: { x: 2, y: 1 },
	atlas: { x: 0.5, y: 0.25 }
};

// ── text utilities (ported from the packed-board renderer) ──────────────────

function clip(text: string, max: number): string {
	if (max <= 0) return '';
	if (text.length <= max) return text;
	return max <= 1 ? text.slice(0, max) : text.slice(0, max - 1) + '…';
}

/** Middle-elide: keeps the start and the end, which for the long labels the
 *  room actually meets (`evt-1787…-coep.attachments`) are the two halves
 *  that identify it. */
function clipMid(text: string, max: number): string {
	if (text.length <= max) return text;
	if (max <= 1) return clip(text, max);
	const head = Math.ceil((max - 1) / 2);
	const tail = max - 1 - head;
	return text.slice(0, head) + '…' + (tail > 0 ? text.slice(-tail) : '');
}

function minutesLabel(iso: string | null, now: number | undefined): string | null {
	if (!iso || now === undefined) return null;
	const t = Date.parse(iso);
	if (Number.isNaN(t)) return null;
	const m = Math.max(0, Math.round((now - t) / 60000));
	return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60 ? String(m % 60) + 'm' : ''}`;
}

function untilLabel(iso: string | null, now: number | undefined): string | null {
	if (now === undefined) return null;
	const text = untilText(iso, now);
	return text?.startsWith('in ') ? text.slice(3) : text;
}

/** Camera controls must yield modified keys to the browser. */
export function isCameraHotkey(e: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey'>): boolean {
	return !e.metaKey && !e.ctrlKey && (e.key === 'f' || e.key === 'a');
}

/** Fallback only — the page measures the real line box off the rendered
 *  board, the same way it already measures `charW`. A constant restating
 *  `.board`'s 12px × 1.35 would be the stylesheet's number stored twice,
 *  and the next person to touch the CSS would silently un-fix the drag. */
export const CAMERA_LINE_HEIGHT_FALLBACK_PX = 16.2;

function wallLabel(seconds: number | null): string | null {
	if (seconds === null || !Number.isFinite(seconds)) return null;
	const m = Math.round(seconds / 60);
	return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60 ? String(m % 60) + 'm' : ''}`;
}

/** Long absolute path tokens → their last two segments (the camera refusing
 * to print a host-absolute path the wire should not have carried). */
export function foldPathTokens(detail: string): string {
	return detail.replace(/(?:\/[\w.@~-]+){3,}/g, (m) => {
		const segs = m.split('/').filter(Boolean);
		return '…/' + segs.slice(-2).join('/');
	});
}

function countsLabel(counts: Record<string, number>): string {
	const order = ['commit', 'merge', 'pr', 'issue', 'kb', 'file', 'comment', 'message', 'reply'];
	const short: Record<string, string> = {
		commit: 'c',
		merge: 'mg',
		pr: 'pr',
		issue: 'is',
		kb: 'kb',
		file: 'f',
		comment: 'cm',
		message: 'msg',
		reply: 're'
	};
	const parts: string[] = [];
	for (const kind of order) {
		const n = counts[kind] ?? 0;
		if (n > 0) parts.push(`${n}${short[kind] ?? kind}`);
	}
	for (const kind of Object.keys(counts).sort()) {
		if (order.includes(kind)) continue;
		if ((counts[kind] ?? 0) > 0) parts.push(`${counts[kind]}${kind}`);
	}
	return parts.join(' ');
}

// ── the canvas ──────────────────────────────────────────────────────────────

class Canvas {
	rows: string[][];
	claimed: boolean[][];
	width: number;
	constructor(width: number, height: number) {
		this.width = width;
		this.rows = Array.from({ length: height }, () => Array(width).fill(' '));
		this.claimed = Array.from({ length: height }, () => Array(width).fill(false));
	}
	put(x: number, y: number, ch: string, claim = true) {
		if (y < 0 || y >= this.rows.length || x < 0 || x >= this.width) return;
		this.rows[y][x] = ch;
		if (claim) this.claimed[y][x] = true;
	}
	/** Ground chars (corridors) never claim — labels overwrite them. */
	ground(x: number, y: number, ch: string) {
		if (y < 0 || y >= this.rows.length || x < 0 || x >= this.width) return;
		if (this.claimed[y][x]) return;
		this.rows[y][x] = ch;
	}
	text(x: number, y: number, s: string) {
		for (let i = 0; i < s.length; i++) this.put(x + i, y, s[i]);
	}
	sea() {
		for (let y = 0; y < this.rows.length; y++) {
			for (let x = 0; x < this.width; x++) {
				if (this.claimed[y][x] || this.rows[y][x] !== ' ') continue;
				const k = (x * 31 + y * 17) % 97;
				if (k === 5) this.rows[y][x] = '·';
				else if (k === 61) this.rows[y][x] = '~';
			}
		}
	}
	toLines(): string[] {
		return this.rows.map((r) => r.join('').replace(/\s+$/, ''));
	}
}

// ── projection ──────────────────────────────────────────────────────────────

interface Frame {
	left: number; // world x at char column 0
	top: number; // world y at char row 0
	sx: number;
	sy: number;
	cols: number;
	rows: number;
}

function frameOf(cam: Camera): Frame {
	const { x: sx, y: sy } = SCALE[cam.level];
	return {
		sx,
		sy,
		cols: cam.cols,
		rows: cam.rows,
		left: cam.center.x - cam.cols / sx / 2,
		top: cam.center.y - cam.rows / sy / 2
	};
}

function toChar(f: Frame, p: Point): { x: number; y: number } {
	return { x: Math.round((p.x - f.left) * f.sx), y: Math.round((p.y - f.top) * f.sy) };
}

function inFrame(f: Frame, c: { x: number; y: number }): boolean {
	return c.x >= 0 && c.x < f.cols && c.y >= 0 && c.y < f.rows;
}

/** Center the camera on a set of places: the bounding box when it fits the
 *  window, else the *first* id (the destination owns the frame). */
export function cameraCenterFor(
	layout: RoomLayout,
	ids: PlaceId[],
	cols: number,
	rows: number,
	level: CameraLevel = 'island'
): Point {
	const pts = ids.map((id) => layout.nodes[id]).filter(Boolean);
	if (pts.length === 0) {
		const b = layout.worldBounds;
		return { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 };
	}
	const minX = Math.min(...pts.map((p) => p.x));
	const maxX = Math.max(...pts.map((p) => p.x));
	const minY = Math.min(...pts.map((p) => p.y));
	const maxY = Math.max(...pts.map((p) => p.y));
	const s = SCALE[level];
	const fitsX = (maxX - minX) * s.x <= cols - 8;
	const fitsY = (maxY - minY) * s.y <= rows - 2;
	if (fitsX && fitsY) return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
	return pts[0];
}

// ── node rendering vocabulary ───────────────────────────────────────────────

/** Per-render lookups the node vocabulary needs but should not recompute
 *  per node: camp spur material and forge-dock produce. */
interface NodeAux {
	campCommits: Map<PlaceId, number>;
	forgeCounts: Map<string, string>;
}

function nodeText(
	node: PlaceNode,
	graph: RoomGraph,
	level: CameraLevel,
	now: number | undefined,
	aux?: NodeAux
): string | null {
	switch (node.kind) {
		case 'repo-root': {
			const short = node.label.includes('/') ? node.label.split('/').pop()! : node.label;
			return `⌂ ${level === 'atlas' ? node.label : short}`;
		}
		case 'directory':
			return `${clipMid(node.label, MAX_DIR_LABEL_CHARS - 1)}/`;
		case 'file':
			return `· ${node.label}`;
		case 'camp': {
			// the camp sits 9 units (18 chars) west of its root: the label must
			// live inside that shore gap or it collides with the root's own.
			// Commits accreted on this spur ride the label — the branch is the
			// place changes pile up before they reach the forge.
			const commits = aux?.campCommits.get(node.id) ?? 0;
			const suffix = commits > 0 ? ` +${commits}c` : '';
			// the suffix survives; the branch name gives way — the shore gap is
			// 18 chars and the material mark is the fresher fact
			return `▛ ${clip(node.label, 15 - suffix.length)}${suffix}`;
		}
		case 'portal-rack':
			return 'P';
		case 'chart-table':
			return 'K';
		case 'strand-bay':
			return 'B';
		case 'watch-perch':
			return 'W';
		case 'wake-dock':
			return 'D';
		case 'cut-loom':
			return 'X';
		case 'work-bench':
			// the shell place (his steer, 2026-08-28): the prompt itself is
			// the glyph — uncategorized commands run here, in plain sight
			return '$';
		case 'test-rig':
			return 'R';
		case 'forge-dock': {
			// the dock shows the island's remote produce — PRs, merges, issues
			// attested by its live actors (counts; the wire has no identities)
			const counts = node.repoId ? aux?.forgeCounts.get(node.repoId) : undefined;
			return counts ? `F FORGE ${counts}` : 'F FORGE';
		}
		case 'home-fixture': {
			if (node.label === 'HOME') return '⌂ HOME';
			if (node.label === 'gate') {
				return graph.pendingLetters > 0 ? `G ◇×${graph.pendingLetters}` : 'G';
			}
			if (node.label === 'watch') {
				// the tower and `brnrd await` are one instrument: an armed wait
				// (`lifecycle: awaiting`) stands a `^` fact with its deadline,
				// and the tower shows the soonest one counting down
				const soonest =
					graph.watch
						.filter((w) => w.mark === '^' && w.until)
						.map((w) => w.until!)
						.sort()[0] ?? null;
				const inWhen = soonest ? untilLabel(soonest, now) : null;
				const base = graph.watch.length > 0 ? `^ ×${graph.watch.length}` : '^';
				return inWhen ? `${base} → ${inWhen}` : base;
			}
			if (node.label === 'clockwork') {
				const next = graph.clockwork
					.filter((e) => e.nextAt)
					.sort((a, b) => (a.nextAt ?? '').localeCompare(b.nextAt ?? ''))[0];
				const inWhen = next ? untilLabel(next.nextAt, now) : null;
				return inWhen ? `T ${inWhen}` : 'T';
			}
			if (node.label === 'garage') {
				const fuel = garageReadings(graph);
				return fuel.length > 0 ? `⛁ ${fuel.join(' · ')}` : '⛁';
			}
			if (node.label === 'library') return 'lib';
			return node.label;
		}
	}
}

const EDGE_CHARS: Record<string, { h: string; v: string }> = {
	tree: { h: '─', v: '│' },
	branch: { h: '═', v: '║' },
	shore: { h: '═', v: '║' },
	control: { h: '┄', v: '┆' },
	'sea-lane': { h: '', v: '' } // sea lanes are open water; traffic marks them
};

// ── the render ──────────────────────────────────────────────────────────────

function bearingArrow(from: Point, to: Point): string {
	const dx = to.x - from.x;
	const dy = to.y - from.y;
	if (Math.abs(dx) > 2 * Math.abs(dy)) return dx > 0 ? '→' : '←';
	if (Math.abs(dy) > 2 * Math.abs(dx)) return dy > 0 ? '↓' : '↑';
	return dy > 0 ? (dx > 0 ? '↘' : '↙') : dx > 0 ? '↗' : '↖';
}

// ── the acts, embodied ──────────────────────────────────────────────────────

/** Stations whose acts were invisible before the pager ceremony: the actor
 *  stood there with a busy status line and a statue's body. */
const STATION_KINDS = new Set<PlaceNodeKind>([
	'portal-rack',
	'chart-table',
	'strand-bay',
	'watch-perch',
	'wake-dock',
	'cut-loom',
	'work-bench'
]);

/** The tether frames of the mind-connect: pager → actor, cycled by the
 *  ceremony's remaining ticks. */
const TETHER_FRAMES = ['⌁', '∿', '≋'];

/** The claw's own mark. Deliberately not `─`: that is the corridor glyph,
 *  and a reach drawn in it is indistinguishable from the terrain it crosses —
 *  by eye and, as the first driven check discovered, by any measurement of
 *  the board. A ceremony you cannot tell apart from the room is a ceremony
 *  you cannot prove is running. */
const CLAW_CHAR = '┈';
const CLAW_TIP = '≻';

/**
 * The visible act at a station: what the actor is doing where it stands.
 * `✎` writing, `☰` reading, `✉` opening correspondence (the deliberate
 * letter-read at the portal rack — a different act from a page arriving).
 * Conservative: no legible act, no mark.
 */
export function activityMark(
	actor: Pick<RoomActor, 'act' | 'detail'>,
	placeKind: PlaceNodeKind | null
): string | null {
	if (!actor.act || !placeKind || !STATION_KINDS.has(placeKind)) return null;
	const leaf = fileFromDetail(actor.detail);
	if (actor.act === 'mutate') return leaf ? `✎ ${leaf}` : '✎';
	if (actor.act === 'orient') {
		if (placeKind === 'portal-rack') return leaf ? `✉ ${leaf}` : '✉';
		return leaf ? `☰ ${leaf}` : null;
	}
	return null;
}

function actorFootline(actor: RoomActor, now: number | undefined): string {
	const until = untilLabel(actor.awaitUntil, now);
	const lifecycle =
		actor.lifecycle === 'awaiting' ? ` (awaiting${until ? ' → ' + until : ''})` : '';
	const pulse = actor.injected ? '  ✉>>>' : '';
	const detail = actor.detail ? foldPathTokens(actor.detail) : null;
	const boundary = [actor.act, detail].filter(Boolean).join(' · ');
	const letters =
		actor.portalsPending > 0
			? `  ◇×${actor.portalsPending}${
					minutesLabel(actor.portalsOldestAt, now)
						? ' oldest ' + minutesLabel(actor.portalsOldestAt, now)
						: ''
				}`
			: '';
	return `${actor.glyph} ${clip(actor.name, 24)}${lifecycle}  ⌁ ${boundary || '—'}${pulse}${letters}`;
}

function chartLine(actor: RoomActor, width: number): string {
	const course = actor.course
		? `course ${actor.course.done}/${actor.course.total}` +
			(actor.course.current ? ` → ${actor.course.current}` : '')
		: '· no chart';
	const produce = countsLabel(Object.fromEntries(actor.relics.map((c) => [c.kind, c.count])));
	const bits = [
		`K ${actor.glyph} ${clip(actor.name, 26)}`,
		course,
		produce || null,
		actor.runner ? `⛁ ${actor.runner}` : null
	].filter(Boolean);
	return clip(bits.join('   '), width);
}

function clothLine(row: ClothRow, width: number, now: number | undefined): string {
	if (row.tense === 'live') {
		const bits = [
			`LIVE ${row.glyph ?? '·'} ${clip(row.name, 28)}`,
			row.course ? `course ${row.course.done}/${row.course.total}` : null,
			countsLabel(row.counts) || null,
			row.childOf ? '↳ strand' : null
		].filter(Boolean);
		return clip(bits.join('  '), width);
	}
	const when = minutesLabel(row.endedAt, now);
	const bits = [
		` cut ${clip(row.name, 28)}`,
		wallLabel(row.wallSeconds),
		row.usd !== null ? `$${row.usd.toFixed(2)}` : null,
		countsLabel(row.counts) || null,
		row.childOf ? '↳ strand' : null,
		when ? `· ${when} ago` : null
	].filter(Boolean);
	return clip(bits.join('  '), width);
}

/**
 * Render the window. Pure and deterministic: same inputs, same bytes.
 * The board (terrain in frame + bearings for what is out of it), then the
 * control rows: actor boundaries, CHARTS, and the Cloth selvage.
 */
export function renderWorld(
	topo: RoomTopology,
	layout: RoomLayout,
	graph: RoomGraph,
	cam: Camera,
	opts: WorldRenderOpts = {}
): string {
	const f = frameOf(cam);
	const now = opts.now;
	const canvas = new Canvas(cam.cols, cam.rows);

	// 1 · corridors — ground, never claiming; labels own their cells
	for (const e of topo.edges) {
		if (cam.level === 'atlas') continue; // atlas shows islands only; every corridor stays below this scale
		const chars = EDGE_CHARS[e.kind];
		if (!chars || chars.h === '') continue;
		const pts = layout.edgeRoutes[`${e.from}->${e.to}`];
		if (!pts) continue;
		for (let i = 0; i + 1 < pts.length; i++) {
			const a = toChar(f, pts[i]);
			const b = toChar(f, pts[i + 1]);
			if (a.x === b.x) {
				for (let y = Math.min(a.y, b.y) + 1; y < Math.max(a.y, b.y); y++)
					canvas.ground(a.x, y, chars.v);
			} else if (a.y === b.y) {
				for (let x = Math.min(a.x, b.x) + 1; x < Math.max(a.x, b.x); x++)
					canvas.ground(x, a.y, chars.h);
			}
		}
	}

	// 2 · the current route, marked on the ground
	if (opts.highlightRoute && opts.highlightRoute.length > 1) {
		for (let i = 0; i + 1 < opts.highlightRoute.length; i++) {
			const key = `${opts.highlightRoute[i]}->${opts.highlightRoute[i + 1]}`;
			const rev = `${opts.highlightRoute[i + 1]}->${opts.highlightRoute[i]}`;
			const pts = layout.edgeRoutes[key] ?? layout.edgeRoutes[rev];
			if (!pts) continue;
			for (let s = 0; s + 1 < pts.length; s++) {
				const a = toChar(f, pts[s]);
				const b = toChar(f, pts[s + 1]);
				if (a.x === b.x) {
					for (let y = Math.min(a.y, b.y); y <= Math.max(a.y, b.y); y++) canvas.ground(a.x, y, '∙');
				} else if (a.y === b.y) {
					for (let x = Math.min(a.x, b.x); x <= Math.max(a.x, b.x); x += 1)
						canvas.ground(x, a.y, '∙');
				}
			}
		}
	}

	// 3 · nodes, in stable paint order (y, then x, then id)
	const aux: NodeAux = {
		campCommits: new Map(
			graph.islands.flatMap((i) =>
				i.camps.map((c) => [campId(i.label, c), c.commits] as [PlaceId, number])
			)
		),
		forgeCounts: new Map(
			graph.islands
				.map((i) => [i.label, countsLabel(i.forge ?? {})] as [string, string])
				.filter(([, s]) => s.length > 0)
		)
	};
	const paintable = Object.values(topo.nodes)
		.map((node) => ({ node, p: layout.nodes[node.id] }))
		.filter((n) => n.p)
		.sort((a, b) => a.p.y - b.p.y || a.p.x - b.p.x || a.node.id.localeCompare(b.node.id));
	const labels: { x: number; y: number; text: string }[] = [];
	for (const { node, p } of paintable) {
		if (cam.level === 'atlas' && node.kind !== 'repo-root' && node.kind !== 'home-fixture')
			continue;
		const c = toChar(f, p);
		if (!inFrame(f, c)) continue;
		const text = nodeText(node, graph, cam.level, now, aux);
		if (text) labels.push({ x: c.x, y: c.y, text });
	}
	// labels never overwrite one another: within a character row, each label
	// clips at the next label's start. Before this, paint order decided who
	// stomped whom — the `src/unts/`-style garble in the 08-27 screenshots
	// was one label's tail under another label's head.
	const byRow = new Map<number, { x: number; y: number; text: string }[]>();
	for (const l of labels) (byRow.get(l.y) ?? byRow.set(l.y, []).get(l.y)!).push(l);
	for (const row of byRow.values()) {
		row.sort((a, b) => a.x - b.x);
		for (let i = 0; i < row.length; i++) {
			const next = row[i + 1];
			const max = next ? next.x - row[i].x - 1 : canvas.width - row[i].x;
			canvas.text(row[i].x, row[i].y, clip(row[i].text, max));
		}
	}

	// 3b · THE CROSSING — the claw, drawn under the actors so a delivery
	// never covers the body receiving it. Ground, like corridors: it uses
	// `canvas.ground` so terrain and labels keep their cells, because a
	// ceremony that erases the room to show itself is a cutscene.
	//
	// The letter is a *claiming* write: it is the one thing in the frame the
	// reader is meant to follow, and it occupies exactly one cell for exactly
	// the beats it is in flight.
	for (const frame of opts.crossings ?? []) {
		frame.arm.forEach((point, i) => {
			const c = toChar(f, point);
			// The leading cell wears the tip. Without it the reach reads as a
			// dotted trail that happens to be there — legible, but it does not
			// say which end is doing the reaching, and the direction is the
			// entire argument for the claw having a source.
			const tip = !frame.settling && i === frame.arm.length - 1;
			canvas.ground(c.x, c.y, tip ? CLAW_TIP : CLAW_CHAR);
		});
		if (frame.letter) {
			const c = toChar(f, frame.letter);
			canvas.text(c.x, c.y, '◇');
		}
	}

	// 3c · THE TERMINAL — the place, drawn on the camp (his 2026-08-28
	// dimensions: "a window rendered on top of the camp, a few lines in
	// height, about 50 in width"). It sits *above* the camp glyph so the
	// actor standing at the camp reads as below it — "which you kinda walk
	// into, and stay below" — and it claims its cells rather than laying
	// down as ground: unlike a corridor or the claw, a window with terrain
	// showing through it is not a window.
	//
	// Anchored to the camp rather than floated at a screen corner on
	// purpose. A panel pinned to the viewport is a HUD, and a HUD is the
	// feed-under-the-map defect wearing a border; the whole argument is that
	// commands happen *somewhere*.
	const termLines = opts.terminal ?? null;
	if (termLines && cam.level !== 'atlas') {
		const campNode = Object.values(topo.nodes).find((n) => n.kind === 'camp');
		const campPos = campNode ? layout.nodes[campNode.id] : undefined;
		if (campPos) {
			const box = terminalBox(termLines);
			const anchor = toChar(f, campPos);
			// one row of air between the floor of the window and the camp it
			// stands on, so the two read as stacked rather than collided
			const top = anchor.y - box.length - 1;
			for (let i = 0; i < box.length; i++) {
				const y = top + i;
				if (y < 0 || y >= cam.rows) continue;
				canvas.text(Math.max(0, anchor.x), y, clip(box[i], cam.cols - Math.max(0, anchor.x)));
			}
		}
	}

	// 4 · actors standing at their places (or mid-walk when the caller
	// passes a display position from an attested transition); stacked when
	// they share one place
	const stacked = new Map<PlaceId, number>();
	const offFrame: string[] = [];
	for (const actor of graph.actors) {
		const pid = topo.actorPlaces[actor.runId];
		const walking = opts.actorPositions?.[actor.runId];
		const p = walking ?? (pid ? layout.nodes[pid] : undefined);
		if (!p) continue;
		const c = toChar(f, p);
		const n = walking ? 0 : (stacked.get(pid) ?? 0);
		if (!walking) stacked.set(pid, n + 1);
		if (!inFrame(f, c)) {
			offFrame.push(
				`${bearingArrow({ x: f.left + f.cols / f.sx / 2, y: f.top + f.rows / f.sy / 2 }, p)} ${actor.glyph} ${clip(actor.name, 18)}`
			);
			continue;
		}
		// the body: the mood face IS the actor when the wire attests one (his
		// call, 2026-08-27 — the face beats the glyph as a body); the glyph
		// remains the handle in the control rows below, and the body for
		// actors with no attested mood.
		const body = actor.moodRest ?? actor.glyph;
		// the mind-connect: an attested injection drops the actor into reading
		// frames in place — the pager at its wrist, the tether cycling. The
		// actor never moves for a page; traffic comes to it.
		const phase = opts.reading?.[actor.runId];
		const tether = phase !== undefined ? `▯${TETHER_FRAMES[phase % TETHER_FRAMES.length]}` : '';
		// the act, embodied: writing/reading marks at the station the actor
		// stands at — a busy status line is not a body.
		const mark = walking ? null : activityMark(actor, pid ? (topo.nodes[pid]?.kind ?? null) : null);
		canvas.text(
			Math.max(0, c.x - 2 - tether.length - n * 8),
			c.y - 1 < 0 ? c.y : c.y - 1,
			`${tether}${body}${mark ? ' ' + mark : ''}`
		);
		// the mind-connect reaches the pager field below the map: a dotted
		// line from the reading actor down through the frame's bottom edge,
		// meeting the PAGER strip that sits directly under it. Ground chars —
		// the tether threads between labels, never through them.
		if (phase !== undefined) {
			for (let y = c.y + 1; y < cam.rows; y++)
				canvas.ground(c.x, y, TETHER_FRAMES[(phase + y) % TETHER_FRAMES.length]);
		}
	}

	// 5 · header: the sea named; account weather on the right
	const weather: string[] = [];
	if (graph.pendingLetters > 0) weather.push(`◇×${graph.pendingLetters}`);
	const strandsOut = graph.actors.filter((a) => a.strand).length;
	if (strandsOut > 0) weather.push(`${strandsOut} strand${strandsOut > 1 ? 's' : ''} out`);
	if (graph.stale) weather.push('wire stale');
	canvas.text(2, 0, cam.level === 'atlas' ? '· · ~ THE ATLAS ~ · ·' : '· · ~ THE SEA ~ · ·');
	const right = weather.join(' · ');
	if (right) canvas.text(Math.max(24, cam.cols - right.length - 2), 0, right);

	// 6 · bearings for what the frame cannot see (never shrink the world in)
	const home = layout.nodes[topo.homeId];
	if (home && !inFrame(f, toChar(f, home))) {
		const arrow = bearingArrow({ x: cam.center.x, y: cam.center.y }, home);
		const bits = [`${arrow} HOME`];
		if (graph.pendingLetters > 0) bits.push(`◇×${graph.pendingLetters}`);
		const next = graph.clockwork
			.filter((e) => e.nextAt)
			.sort((a, b) => (a.nextAt ?? '').localeCompare(b.nextAt ?? ''))[0];
		const inWhen = next ? untilLabel(next.nextAt, now) : null;
		if (inWhen) bits.push(`T ${inWhen}`);
		const fuel = garageReadings(graph).map((reading) => `⛁ ${reading}`);
		bits.push(...fuel.slice(0, 2));
		offFrame.unshift(bits.join(' · '));
	}
	if (offFrame.length > 0) canvas.text(2, cam.rows - 1, clip(offFrame.join('   '), cam.cols - 4));

	canvas.sea();
	const out = canvas.toLines();
	out.push('');

	// 7 · the pager field — boundary-injection status, in two tenses,
	// sitting directly between the map and the text rows so the reading
	// tether has somewhere to land (his ask, 2026-08-27). WAITING is what
	// has accumulated and not yet been injected (the wire attests count +
	// oldest age, never content); READ is the injected boundaries, newest
	// first. A page still names only its carrier — the fence holds.
	if (graph.actors.length > 0) {
		const readingNow = new Set(Object.keys(opts.reading ?? {}));
		const oldest = graph.actors
			.map((a) => a.portalsOldestAt)
			.filter((t): t is string => !!t)
			.sort()[0];
		const oldestIn = oldest ? minutesLabel(oldest, now) : null;
		const waiting =
			graph.pendingLetters > 0
				? `◇×${graph.pendingLetters} waiting${oldestIn ? ' · oldest ' + oldestIn : ''}`
				: '· nothing waiting';
		const pages = opts.pages ?? [];
		const readCount = pages.length > 0 ? `✉×${pages.length} read` : '✉ none read yet';
		const plug = readingNow.size > 0 ? '▯⌁' : '▯';
		out.push(clip(`${plug} PAGER   ${waiting}   ${readCount}`, cam.cols));
		// THE CONDITION LINE. The pager read out the *log* and nothing about
		// the body carrying it, which is the difference between a feed and a
		// worn device — "it should be your diegetic device, shown to a user"
		// (maintainer, 2026-08-28). Fuel is the binding ceiling **with its
		// reset clock**, because "10% resetting in 40 minutes" and "10%
		// resetting in three days" are opposite instructions and the
		// percentage alone cannot tell them apart (his own fourth question,
		// same day). Slots say how much more body is available, not just how
		// much is busy.
		const condition = conditionLine(graph, now);
		if (condition) out.push(clip(condition, cam.cols));
		// THE PAGE IS THE BLOCK. It used to be the carrier — `rode mutate ·
		// cat > RailBench.svelte` — because the carrier was the only thing
		// left on the wire after `bool(record.get("inject"))` ate the
		// injection daemon-side. So the device built to show "the accumulated
		// block that you gonna get injected at the boundary" showed the
		// command log instead, and was reported wrong four times.
		//
		// The carrier is not deleted — "the action log is bad, it is actually
		// good, it is just not what you get injected" (maintainer,
		// 2026-08-27). It rides a continuation line under the page **in
		// transit only**: that is the one page a reader is being asked to
		// watch, and giving all three a second row would trade the map for
		// the strip. Its permanent home is the terminal over the camp
		// (design-the-crossing.md rung 4), not here.
		const marked = new Set<string>();
		for (const p of pages.slice(0, 3)) {
			const hhmm = p.at.length >= 16 ? p.at.slice(11, 16) : p.at;
			const carrier = [p.act, p.detail ? foldPathTokens(p.detail) : null]
				.filter(Boolean)
				.join(' · ');
			// ▸ the page being read right now: its actor is mid-ceremony and
			// this is that actor's newest page — the waiting → read transit
			const fresh = readingNow.has(p.runId) && !marked.has(p.runId);
			marked.add(p.runId);
			// An absent block is not an empty one: a daemon predating the
			// `injection` wire field publishes the bool alone, and the row
			// falls back to the carrier rather than rendering a blank page
			// that would read as "nothing was injected".
			const body = p.injection ?? (carrier ? `rode ${carrier}` : 'a boundary');
			out.push(clip(`  ${fresh ? '▸' : ' '} ${hhmm} ✉ ${p.glyph} ${body}`, cam.cols));
			if (fresh && p.injection && carrier) {
				out.push(clip(`        ↳ rode ${carrier}`, cam.cols));
			}
		}
		if (pages.length > 3) out.push(clip(`    … ${pages.length - 3} older`, cam.cols));
		out.push('');
		for (const actor of graph.actors) out.push(clip(actorFootline(actor, now), cam.cols));
		out.push('');
		out.push('CHARTS');
		for (const actor of graph.actors) out.push(chartLine(actor, cam.cols));
		out.push('');
	}

	// 8 · the Cloth selvage: live rows + a short history tail
	const live = graph.cloth.filter((r) => r.tense === 'live');
	const cut = graph.cloth.filter((r) => r.tense === 'cut').slice(0, opts.clothRows ?? 4);
	if (live.length > 0 || cut.length > 0) {
		out.push('══ CLOTH ' + '═'.repeat(Math.max(0, cam.cols - 9)));
		for (const row of live) out.push(clothLine(row, cam.cols, now));
		if (cut.length > 0) {
			out.push('──── CUT ' + '─'.repeat(Math.max(0, cam.cols - 9)));
			for (const row of cut) out.push(clothLine(row, cam.cols, now));
		}
	}

	return out.join('\n');
}

/** The legend, as its own block so the page can render it apart.
 *  `⌂` currently names two different kinds — an island root and HOME — and
 *  the legend says so by printing them together rather than by carrying a
 *  note about it. A legend names what is on screen; a legend that explains
 *  its own open questions to the reader has become a TODO with an
 *  audience. Splitting the glyph is a visual-design call, not this one. */
export const LEGEND = [
	'the mood face is the resident (@ when faceless)   a…z strands   ◇ pending letter   ✉>>> boundary injection',
	'⌂ island root · ⌂ HOME   name/ chamber   · file leaf   ▛ camp   lib library   ∙ current route',
	'P portal  K chart  B bay  W watch  D wake  X cut  $ bench (uncategorized shell work)  R rig  F FORGE (+pr/mg/is counts)',
	'─│ corridors  ═║ branch/shore rail  ┄┆ station tether  G gate (HOME)  ▛ camp +Nc commits',
	'^ watch — armed `brnrd await`s count down here  T clockwork  ⛁ garage  arrows = off-camera bearings',
	'⌁ attested boundary   ══ CLOTH time register — live, then history',
	'┈≻ the claw — a letter carried from HOME to the actor that received it   ◇ the letter, in flight',
	'▯⌁@ mind-connect — reading the pager   ✎ writing  ☰ reading  ✉ opening a letter',
	'▯ PAGER — injection status: ◇ waiting (accumulated, not yet injected) · ✉ read · ▸ in transit',
	'  a read page shows the injected block itself; ↳ names the boundary that carried it'
].join('\n');
