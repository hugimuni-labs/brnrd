// THE TERMINAL — the place the actor walks into, not a feed at the bottom.
//
// "commands should move to the terminal, the terminal can have a place
// somewhere, maybe on top, or embedded in the camp (resembling the
// hitchhiker's guide mega computer, the 42 one, which you kinda walk into,
// and stay below, while some interaction animation goes)" — maintainer,
// 2026-08-28, approving the shape argued in `design-the-crossing.md`
// §The vertical axis. Earlier the same day, with the dimensions:
// "maybe move the terminal as a window rendered on top of the camp, a few
// lines in height, about 50 in width?"
//
// **The split section is not a section. It is a place.** That is what made
// the log feel wrong: commands are not a feed, they are what happens
// *inside* a structure the actor stands in, and a feed of them under the
// map is the log describing a room it is not in. Crossings are traffic and
// belong to the mothership's channel (the pager); commands are labour and
// belong here.
//
// The place already existed in the vocabulary and had simply never been
// built out — `$`, the bench, defined as where an act whose boundary names
// no legible resource stands the actor. Exactly these rows.
//
// Why a store of its own rather than the trail: the trail is *footsteps in
// terrain* and drops any boundary whose directory will not resolve
// (`+page.svelte`: `if (!dir || !at) continue`). A command log that silently
// omitted every command run from an unresolvable cwd would be the same
// narrowing this room keeps shipping. This records the boundary, not the
// footstep.

import type { LiveRun } from './liveRuns.ts';

/** One command, as the terminal shows it. */
export interface TerminalLine {
	/** Boundary timestamp — the dedupe key, as in the pager. */
	at: string;
	act: string | null;
	detail: string | null;
}

/** His width, near enough: "about 50 in width". The frame costs two cells,
 *  so the body is 48 and a command that overruns says so with an ellipsis —
 *  **a terminal has a floor, so it bounds itself**, and unlike a feed,
 *  running out of room is legible rather than a truncation nobody sees. */
export const TERMINAL_COLS = 50;

/** "a few lines in height". Five body rows plus the frame: enough to read a
 *  thought's worth of labour without the window becoming the map. */
export const TERMINAL_ROWS = 5;

/** Kept per run — a scrollback, not an archive. */
export const TERMINAL_CAP = 40;

/**
 * Accumulate commands from attested boundaries into `store` (mutated; the
 * caller owns persistence, same contract as the trail and the pager).
 *
 * Every boundary counts, not only injected ones: the pager's own filter
 * (`edge.injected`) is what makes it the *mothership's* channel, and
 * applying it here too would leave the terminal showing the same eight rows
 * under a different frame.
 */
export function recordCommands(
	runs: Pick<LiveRun, 'run_id' | 'edge'>[],
	store: Record<string, TerminalLine[]>
): TerminalLine[] {
	const fresh: TerminalLine[] = [];
	for (const run of runs) {
		const edge = run.edge;
		const at = edge?.at ?? null;
		if (!edge || !at) continue;
		const feed = (store[run.run_id] ??= []);
		if (feed.some((l) => l.at === at)) continue;
		const line: TerminalLine = { at, act: edge.act ?? null, detail: edge.detail ?? null };
		feed.push(line);
		if (feed.length > TERMINAL_CAP) feed.splice(0, feed.length - TERMINAL_CAP);
		fresh.push(line);
	}
	return fresh;
}

/** Drop runs that have left the wire — the same scoping the pager needed
 *  once `✉×151 read` turned out to be counting runs that ended days ago. */
export function terminalFeed(
	store: Record<string, TerminalLine[]>,
	runId: string,
	liveRunIds?: Iterable<string>
): TerminalLine[] {
	if (liveRunIds && ![...liveRunIds].includes(runId)) return [];
	return [...(store[runId] ?? [])].reverse();
}

/**
 * The window's rendered rows — frame included, newest command at the top.
 *
 * Pure and clock-free so the flash diff can ride it: the same store renders
 * the same box, and a row changes only when a boundary actually landed.
 */
export function terminalBox(
	lines: TerminalLine[],
	opts: { cols?: number; rows?: number; title?: string } = {}
): string[] {
	const cols = Math.max(8, opts.cols ?? TERMINAL_COLS);
	const rows = Math.max(1, opts.rows ?? TERMINAL_ROWS);
	const inner = cols - 2;
	const title = ` ${opts.title ?? '$ bench'} `;
	const head = `┌${title}${'─'.repeat(Math.max(0, inner - title.length))}┐`.slice(0, cols);
	const out = [head];
	const body = lines.slice(0, rows);
	for (const line of body) {
		const text = [line.act, line.detail].filter(Boolean).join(' · ');
		out.push(`│${fit(text || 'a boundary', inner)}│`);
	}
	// An empty terminal says so. A window with no floor showing is a room
	// the reader cannot tell from a broken one.
	for (let i = body.length; i < rows; i++) {
		out.push(`│${fit(i === 0 && lines.length === 0 ? 'no commands yet' : '', inner)}│`);
	}
	const more = lines.length > rows ? ` ${lines.length - rows} older ` : '';
	out.push(`└${more}${'─'.repeat(Math.max(0, inner - more.length))}┘`.slice(0, cols));
	return out;
}

/** Pad or ellipsis-clip to exactly `width`. */
function fit(text: string, width: number): string {
	if (text.length === width) return text;
	if (text.length < width) return text + ' '.repeat(width - text.length);
	return width <= 1 ? text.slice(0, width) : text.slice(0, width - 1) + '…';
}
