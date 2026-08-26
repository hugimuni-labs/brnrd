// The ASCII reference camera — the room painted as a character plane
// (design-room-operational-topology.md §ASCII reference renderer; repainted
// 2026-08-26 on the maintainer's live steer: a map you scan, never a report
// in monospace — Cogmind's grid, not a card stack).
//
// The camera paints a 2D char canvas: the sea as ground, repo islands
// *placed* on it, the forge on the coast, the gate at the shore. Actors
// stand inside the geography their attested boundary resolves to. Below the
// map, CHARTS and CLOTH stay rows on purpose — the doc's own rule: course,
// promises and spend are control state, NOT terrain; painting them as
// geography is the first room's mistake wearing a new skin.
//
// Deliberately plain, pure, deterministic: same graph + same `now` ⇒ same
// board byte for byte (acceptance invariant #3). It draws only what the
// graph attests — no interpolation, no ambient life, no fabricated
// envelopes. A prettier renderer must consume the same `RoomGraph` and tell
// the same story.

import type { Place, RoomActor, RoomGraph, ClothRow } from './roomGraph.ts';

export interface RenderOpts {
	/** Board width in characters. */
	width?: number;
	/** Wall clock for elapsed-time labels; omit ⇒ no elapsed labels. */
	now?: number;
	/** Cap on cut Cloth rows. */
	clothRows?: number;
}

const DEFAULT_WIDTH = 76;

// ── text utilities ──────────────────────────────────────────────────────────

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

/** Distance to a *future* time — the await deadline; age math reads 0m. */
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

// ── vocabulary ──────────────────────────────────────────────────────────────

/** Long absolute path tokens → their last two segments. The publisher
 * relativizes `edge.dir` but `edge.detail` can still carry a host-absolute
 * path (seen live 2026-08-26: an Edit's full /Users/… path on the wire —
 * named as a daemon-side gap; this is the camera refusing to print it). */
export function foldPathTokens(detail: string): string {
	return detail.replace(/(?:\/[\w.@~-]+){3,}/g, (m) => {
		const segs = m.split('/').filter(Boolean);
		return '…/' + segs.slice(-2).join('/');
	});
}

/** The stance word for a place. A chamber renders as its path (the noun IS
 * the place); stations wear their name. */
export function placeLabel(place: Place): string {
	switch (place.kind) {
		case 'chamber':
			return place.label ?? 'the tree';
		case 'test-rig':
			// The label is the chamber the rig is attached to; when the wire
			// only gave the command, the boundary line below already says it.
			return `RIG${place.label && !place.label.includes(' ') ? ' ' + place.label : ''}`;
		case 'forge-dock':
			return 'FORGE';
		case 'correspondence-desk':
			return 'DESK';
		case 'chart-table':
			return 'CHART';
		case 'strand-bay':
			return 'BAY';
		case 'watch-point':
			return 'WATCH';
		case 'wake-dock':
			return 'WAKE DOCK';
		case 'cut-line':
			return 'CUT LINE';
	}
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
	text(x: number, y: number, s: string) {
		if (y < 0 || y >= this.rows.length) return;
		for (let i = 0; i < s.length && x + i < this.width; i++) {
			if (x + i >= 0) {
				this.rows[y][x + i] = s[i];
				this.claimed[y][x + i] = true;
			}
		}
	}
	/** Reserve a rectangle (a block's whole footprint) so the sea never
	 *  ripples inside it. */
	claim(x0: number, y0: number, w: number, h: number) {
		for (let y = y0; y < y0 + h && y < this.rows.length; y++) {
			for (let x = x0; x < x0 + w && x < this.width; x++) {
				if (x >= 0 && y >= 0) this.claimed[y][x] = true;
			}
		}
	}
	/** Sea texture: deterministic sparse ripple, only over unclaimed water. */
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

interface Block {
	label: string;
	lines: string[];
	/** Double border for islands, single for civic machinery. */
	civic: boolean;
	w: number;
	h: number;
}

function block(label: string, lines: string[], civic: boolean, maxW: number): Block {
	const w = Math.min(maxW, Math.max(label.length + 6, ...lines.map((l) => l.length + 4), 18));
	return { label, lines, civic, w, h: lines.length + 2 };
}

function paintBlock(c: Canvas, b: Block, x: number, y: number) {
	c.claim(x, y, b.w, b.h);
	const [tl, tr, bl, br, hz, vt] = b.civic
		? ['┌', '┐', '└', '┘', '─', '│']
		: ['╔', '╗', '╚', '╝', '═', '║'];
	const head = ` ${b.label} `;
	const trail = Math.max(0, b.w - head.length - 3);
	c.text(x, y, `${tl}${hz}${clip(head, b.w - 3)}${hz.repeat(trail)}${tr}`);
	for (let i = 0; i < b.lines.length; i++) {
		c.text(x, y + 1 + i, vt);
		c.text(x + 2, y + 1 + i, clip(b.lines[i], b.w - 3));
		c.text(x + b.w - 1, y + 1 + i, vt);
	}
	c.text(x, y + b.h - 1, `${bl}${hz.repeat(b.w - 2)}${br}`);
}

// ── content builders ────────────────────────────────────────────────────────

/** An actor as it stands in geography: glyph+face, stance, then the attested
 * boundary (verb · redacted detail) with the injection pulse when the world
 * folded in at that boundary, then the run's own letter rack. */
function actorLines(actor: RoomActor, now: number | undefined): string[] {
	const out: string[] = [];
	const face = actor.moodRest ? ` ${actor.moodRest}` : '';
	const until = untilLabel(actor.awaitUntil, now);
	const lifecycle =
		actor.lifecycle === 'awaiting' ? ` (awaiting${until ? ' → ' + until : ''})` : '';
	out.push(`  ${actor.glyph}${face}  ${placeLabel(actor.place)}${lifecycle}`);
	if (actor.act || actor.detail) {
		const pulse = actor.injected ? '  ✉>>>' : '';
		const detail = actor.detail ? foldPathTokens(actor.detail) : null;
		out.push(`     ⌁ ${[actor.act, detail].filter(Boolean).join(' · ')}${pulse}`);
	}
	if (actor.portalsPending > 0) {
		const age = minutesLabel(actor.portalsOldestAt, now);
		out.push(`     ◇×${actor.portalsPending} resting at the rack${age ? ` · oldest ${age}` : ''}`);
	}
	return out;
}

/** Actors standing on their island (every place that is a local station of
 * the camp). Forge-dock actors stand at the forge; wake-dock/cut-line at
 * the shore. */
function onIsland(a: RoomActor): boolean {
	return !['forge-dock', 'wake-dock', 'cut-line'].includes(a.place.kind);
}

function islandBlock(
	island: RoomGraph['islands'][number],
	actors: RoomActor[],
	now: number | undefined,
	maxW: number
): Block {
	const lines: string[] = ['trunk ' + '═'.repeat(18)];
	if (island.camps.length === 0) lines.push(' dormant · no camp, no actor');
	for (const camp of island.camps) {
		const where = camp.dir ?? (camp.env === 'host' ? 'the shared checkout' : null);
		lines.push(` └ ${camp.branch ?? '(no branch attested)'}${where ? ' · ' + where : ''}`);
		for (const glyph of camp.actorGlyphs) {
			const actor = actors.find(
				(a) => a.glyph === glyph && a.islandLabel === island.label && onIsland(a)
			);
			if (actor) lines.push(...actorLines(actor, now));
		}
	}
	return block(island.label, lines, false, maxW);
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

// ── the board ───────────────────────────────────────────────────────────────

/**
 * Paint the whole board: sea header → the map (islands packed onto the
 * plane, forge on the coast, gate at the shore) → CHARTS → CLOTH. Sections
 * with nothing attested collapse; sparse live state reads *quiet*, never
 * broken.
 */
export function renderRoomGraph(graph: RoomGraph, opts: RenderOpts = {}): string {
	const width = Math.max(48, opts.width ?? DEFAULT_WIDTH);
	const now = opts.now;
	const maxBlockW = width - 4;

	// build blocks
	const blocks: Block[] = graph.islands.map((island) =>
		islandBlock(island, graph.actors, now, maxBlockW)
	);
	const forgeActors = graph.actors.filter((a) => a.place.kind === 'forge-dock');
	if (forgeActors.length > 0) {
		const lines = forgeActors.flatMap((a) => actorLines(a, now));
		blocks.push(block('FORGE', lines, true, maxBlockW));
	}

	// pack blocks onto the plane, left→right, wrapping; the sea fills around
	let x = 2;
	let y = 2;
	let rowH = 0;
	const placed: { b: Block; x: number; y: number }[] = [];
	for (const b of blocks) {
		if (x > 2 && x + b.w > width - 1) {
			x = 2;
			y += rowH + 1;
			rowH = 0;
		}
		placed.push({ b, x, y });
		x += b.w + 3;
		rowH = Math.max(rowH, b.h);
	}
	const mapH = placed.length > 0 ? Math.max(...placed.map((p) => p.y + p.b.h)) + 1 : 3;

	const canvas = new Canvas(width, mapH + 1);
	// header: the sea named, the watch facts on the right
	const watch: string[] = [];
	if (graph.pendingLetters > 0)
		watch.push(`! ${graph.pendingLetters} letter${graph.pendingLetters > 1 ? 's' : ''}`);
	const strandsOut = graph.actors.filter((a) => a.strand).length;
	if (strandsOut > 0) watch.push(`${strandsOut} strand${strandsOut > 1 ? 's' : ''} out`);
	if (graph.stale) watch.push('wire stale');
	canvas.text(2, 0, '· · ~ THE SEA ~ · ·');
	const right = watch.join(' · ');
	if (right) canvas.text(Math.max(24, width - right.length - 2), 0, right);

	for (const p of placed) paintBlock(canvas, p.b, p.x, p.y);
	if (placed.length === 0) canvas.text(2, 2, '(no ground attested — no live runs, no ledger)');

	// the gate at the shore — letters resting, shore-state actors
	const shoreActors = graph.actors.filter((a) => ['wake-dock', 'cut-line'].includes(a.place.kind));
	const gateBits = [
		graph.pendingLetters > 0
			? `G ◇×${graph.pendingLetters} resting`
			: graph.actors.length === 0
				? `G · quiet${graph.daemonMood ? ` · daemon ${graph.daemonMood.state}` : ''}`
				: 'G ·',
		...shoreActors.map((a) => `${a.glyph} at the ${placeLabel(a.place).toLowerCase()}`)
	];
	canvas.text(2, mapH, gateBits.join('   '));
	canvas.sea();

	const out: string[] = canvas.toLines();
	out.push('');

	// control state — rows on purpose: intent and cost are not terrain
	if (graph.actors.length > 0) {
		out.push('CHARTS');
		for (const actor of graph.actors) out.push(chartLine(actor, width));
		out.push('');
	}

	// time — the Cloth register
	const live = graph.cloth.filter((r) => r.tense === 'live');
	const cut = graph.cloth.filter((r) => r.tense === 'cut').slice(0, opts.clothRows ?? 8);
	if (live.length > 0 || cut.length > 0) {
		out.push('══ CLOTH ' + '═'.repeat(Math.max(0, width - 9)));
		for (const row of live) out.push(clothLine(row, width, now));
		if (cut.length > 0) {
			out.push('──── CUT ' + '─'.repeat(Math.max(0, width - 9)));
			for (const row of cut) out.push(clothLine(row, width, now));
		}
	}

	return out.join('\n');
}

/** The legend, as its own block so the page can render it apart. */
export const LEGEND = [
	'@ resident   a…z strands   ◇ pending letter   ✉>>> boundary injection',
	'⌁ attested boundary (verb · redacted detail)   K chart (Now/course)',
	'G gate   RIG local probe   FORGE the coast   DESK correspondence',
	'CHART card edits   BAY dispatch   WATCH await   ══ CLOTH time register'
].join('\n');
