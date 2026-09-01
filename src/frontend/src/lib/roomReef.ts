// The reef — kb outcrops beside the work that cited them
// (design-the-water-line.md "The cloth is the home — the weave rework":
// "The kb is the reef. It only grows, it is never in-flight (a kb page is
// committed or does not exist), so it accretes below the surface — visible
// through the water, pressable, never floating. The tome glyph he proposed
// marks reef outcrops beside the work that cited them.").
//
// Pure projection over a compiled RoomGraph's `cloth` — one row per run,
// live and cut already deduplicated there (`roomGraph.ts`'s own "one run,
// one row" rule). This module adds nothing the graph didn't already carry
// on `ClothRow.kbPages`; it only re-keys by *page* instead of by *run*, and
// joins the two directions the doc's own sentence asks for: page → the
// run(s) that cited it.
//
// **Growth, not eviction.** Every page any cloth row cites becomes an
// outcrop; nothing here ever drops one because it's old (no "recent kb
// pages" window — the spec's own hard rule). `REEF_RENDER_MAX` bounds only
// what one compile call *returns* to a renderer; `droppedOlder` says how
// many more exist, on purpose, so a renderer that must draw a bounded scene
// never reads as having drawn everything.
//
// **Depth is an open question this wire cannot fully answer yet.** "Older
// = deeper" is the spec's rule, but nothing on the wire carries a kb page's
// real commit time — only the *citing run's* own timestamp, and only a cut
// run has one that's real (`ClothRow.endedAt`, from the closed-run ledger).
// A page cited only by a still-live run has no attested time at all: it is
// already sunk (committed, by the doctrine's own bar — see
// `relics.live_portal_kb_pages`'s docstring), just not *dateable* from here.
// This module's choice, named rather than buried: undated outcrops render
// shallowest (the freshest evidence this wire has, even though the page
// itself may be older), and dated outcrops sort by their citing run's own
// close time — a proxy for "when it sank," not the page's true history.
// Getting the real answer needs a kb-page-commit timestamp on the wire,
// which nothing here invents.

import type { RoomGraph, ClothRow, ClothKbPage } from './roomGraph.ts';

/** One run's row citing an outcrop — the join the doc's sentence names:
 *  "outcrops beside the work that cited them." */
export interface ReefCitation {
	runId: string | null;
	name: string;
	/** `@`/`a`…`z` for a still-live actor, `null` for a cut-only row (the
	 *  glyph doesn't survive past the live board). */
	glyph: string | null;
	tense: 'live' | 'cut';
}

export interface ReefOutcrop {
	path: string;
	/** `null` when no citing row carried a derivable link. Every citing
	 *  row names the same page, so a real url from any of them is as good
	 *  as another; the last one visited (iteration order, not chronology)
	 *  wins when more than one is present. */
	url: string | null;
	/** page → the run(s) that cited it, newest citation first. */
	citations: ReefCitation[];
	/** The oldest dated citation's close time (ISO), or `null` when every
	 *  citation citing this page is still live — see the module docstring's
	 *  "Depth is an open question" note. Not the page's own commit time. */
	depthAt: string | null;
}

/** Rendered-set bound (display only — see the module docstring; the model
 * itself never evicts). Generous: a reef with more than this many distinct
 * committed pages is a real reef, and the renderer needs to say so rather
 * than silently show a partial one. */
export const REEF_RENDER_MAX = 40;

export interface CompiledReef {
	/** Shallowest first: undated outcrops (live-only citations) lead, then
	 *  dated outcrops newest-close-time first, oldest (deepest) last. */
	outcrops: ReefOutcrop[];
	/** Outcrops that exist but fall past `REEF_RENDER_MAX` — always stated,
	 *  never silently dropped (the spec's own "+N older" rule). */
	droppedOlder: number;
}

function citationOf(row: ClothRow): ReefCitation {
	return { runId: row.runId, name: row.name, glyph: row.glyph, tense: row.tense };
}

/**
 * Compile the reef from a RoomGraph's cloth. Pure, deterministic, no clock
 * reads — every fact traces to a `ClothRow.kbPages` entry the graph already
 * joined from an attested wire field (`roomGraph.ts`'s own contract).
 *
 * A run with no kb produce contributes no outcrop and needs no special
 * case: the loop below only ever visits pages a row actually names.
 */
export function compileRoomReef(graph: RoomGraph): CompiledReef {
	const byPath = new Map<string, ReefOutcrop>();
	for (const row of graph.cloth) {
		for (const page of row.kbPages) {
			if (!page.path) continue;
			const outcrop = byPath.get(page.path) ?? {
				path: page.path,
				url: null,
				citations: [],
				depthAt: null
			};
			if (!byPath.has(page.path)) byPath.set(page.path, outcrop);
			applyCitation(outcrop, page, row);
		}
	}
	const all = [...byPath.values()].sort(compareShallowestFirst);
	const outcrops = all.slice(0, REEF_RENDER_MAX);
	return { outcrops, droppedOlder: Math.max(0, all.length - outcrops.length) };
}

function applyCitation(outcrop: ReefOutcrop, page: ClothKbPage, row: ClothRow): void {
	if (page.url) outcrop.url = page.url;
	outcrop.citations.push(citationOf(row));
	// The *earliest* attested close time among citations is the best lower
	// bound this wire has on "how long this page has existed" — the page
	// was already committed by (at latest) whichever citing run closed
	// first, so the oldest citation, not the newest, is the depth anchor.
	if (row.endedAt && (outcrop.depthAt === null || row.endedAt < outcrop.depthAt)) {
		outcrop.depthAt = row.endedAt;
	}
}

/** Undated (live-only) outcrops first; among dated ones, newest close time
 * first (oldest — deepest — last). Ties broken by path so the order is
 * stable across compiles of the same graph. */
function compareShallowestFirst(a: ReefOutcrop, b: ReefOutcrop): number {
	if (a.depthAt === null && b.depthAt === null) return a.path.localeCompare(b.path);
	if (a.depthAt === null) return -1;
	if (b.depthAt === null) return 1;
	if (a.depthAt !== b.depthAt) return a.depthAt > b.depthAt ? -1 : 1;
	return a.path.localeCompare(b.path);
}
