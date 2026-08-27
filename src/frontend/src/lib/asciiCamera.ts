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

import type { RoomActor, RoomGraph, ClothRow } from './roomGraph.ts';
import type { PlaceId, PlaceNode, RoomTopology } from './roomTopology.ts';
import type { Point, RoomLayout } from './roomLayout.ts';

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
	/** Cap on cut Cloth rows below the board. */
	clothRows?: number;
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

function minutesLabel(iso: string | null, now: number | undefined): string | null {
	if (!iso || now === undefined) return null;
	const t = Date.parse(iso);
	if (Number.isNaN(t)) return null;
	const m = Math.max(0, Math.round((now - t) / 60000));
	return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60 ? String(m % 60) + 'm' : ''}`;
}

function untilLabel(iso: string | null, now: number | undefined): string | null {
	if (!iso || now === undefined) return null;
	const t = Date.parse(iso);
	if (Number.isNaN(t)) return null;
	const m = Math.max(0, Math.round((t - now) / 60000));
	return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60 ? String(m % 60) + 'm' : ''}`;
}

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

function nodeText(
	node: PlaceNode,
	graph: RoomGraph,
	level: CameraLevel,
	now: number | undefined
): string | null {
	switch (node.kind) {
		case 'repo-root': {
			const short = node.label.includes('/') ? node.label.split('/').pop()! : node.label;
			return `⌂ ${level === 'atlas' ? node.label : short}`;
		}
		case 'directory':
			return `${node.label}/`;
		case 'file':
			return `· ${node.label}`;
		case 'camp':
			// the camp sits 9 units (18 chars) west of its root: the label must
			// live inside that shore gap or it collides with the root's own
			return `▛ ${clip(node.label, 15)}`;
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
		case 'test-rig':
			return 'R';
		case 'forge-dock':
			return 'F FORGE';
		case 'home-fixture': {
			if (node.label === 'HOME') return '⌂ HOME';
			if (node.label === 'gate') {
				return graph.pendingLetters > 0 ? `G ◇×${graph.pendingLetters}` : 'G';
			}
			if (node.label === 'watch') return graph.watch.length > 0 ? `^ ×${graph.watch.length}` : '^';
			if (node.label === 'clockwork') {
				const next = graph.clockwork
					.filter((e) => e.nextAt)
					.sort((a, b) => (a.nextAt ?? '').localeCompare(b.nextAt ?? ''))[0];
				const inWhen = next ? untilLabel(next.nextAt, now) : null;
				return inWhen ? `T ${inWhen}` : 'T';
			}
			if (node.label === 'garage') return '⛁';
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
		const chars = EDGE_CHARS[e.kind];
		if (!chars || chars.h === '') continue;
		if (cam.level === 'atlas' && e.kind !== 'sea-lane') continue; // atlas shows islands, not corridors
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
	const paintable = Object.values(topo.nodes)
		.map((node) => ({ node, p: layout.nodes[node.id] }))
		.filter((n) => n.p)
		.sort((a, b) => a.p.y - b.p.y || a.p.x - b.p.x || a.node.id.localeCompare(b.node.id));
	for (const { node, p } of paintable) {
		if (cam.level === 'atlas' && node.kind !== 'repo-root' && node.kind !== 'home-fixture')
			continue;
		const c = toChar(f, p);
		if (!inFrame(f, c)) continue;
		const text = nodeText(node, graph, cam.level, now);
		if (text) canvas.text(c.x, c.y, text);
	}

	// 4 · actors standing at their places; stacked when they share one
	const stacked = new Map<PlaceId, number>();
	const offFrame: string[] = [];
	for (const actor of graph.actors) {
		const pid = topo.actorPlaces[actor.runId];
		const p = pid ? layout.nodes[pid] : undefined;
		if (!p) continue;
		const c = toChar(f, p);
		const n = stacked.get(pid) ?? 0;
		stacked.set(pid, n + 1);
		if (!inFrame(f, c)) {
			offFrame.push(
				`${bearingArrow({ x: f.left + f.cols / f.sx / 2, y: f.top + f.rows / f.sy / 2 }, p)} ${actor.glyph} ${clip(actor.name, 18)}`
			);
			continue;
		}
		const face = actor.moodRest ? ` ${actor.moodRest}` : '';
		canvas.text(Math.max(0, c.x - 2 - n * 8), c.y - 1 < 0 ? c.y : c.y - 1, `${actor.glyph}${face}`);
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
		const fuel = graph.garage
			.filter((g) => g.windows.some((w) => w.percent !== null))
			.map((g) => {
				const w = g.windows.find((w) => w.percent !== null);
				return `⛁ ${g.shell} ${Math.round(w!.percent!)}%`;
			});
		bits.push(...fuel.slice(0, 2));
		offFrame.unshift(bits.join(' · '));
	}
	if (offFrame.length > 0) canvas.text(2, cam.rows - 1, clip(offFrame.join('   '), cam.cols - 4));

	canvas.sea();
	const out = canvas.toLines();
	out.push('');

	// 7 · control rows — deliberately not terrain
	if (graph.actors.length > 0) {
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

/** The legend, as its own block so the page can render it apart. */
export const LEGEND = [
	'@ resident   a…z strands   ◇ pending letter   ✉>>> boundary injection',
	'⌂ island root   name/ chamber   · file leaf   ▛ camp   ∙ current route',
	'P portal  K chart  B bay  W watch  D wake  X cut  R rig  F FORGE',
	'─│ corridors  ═║ branch/shore rail  ┄┆ station tether  G gate (HOME)',
	'^ watch  T clockwork  ⛁ garage   arrows = off-camera bearings',
	'⌁ attested boundary   ══ CLOTH time register — live, then history'
].join('\n');
