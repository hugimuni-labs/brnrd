// The strand rail (board.html rail()): which strands it shows, a state move logged as `HH:MM:SS thread was→now` (the
// last six kept), and the face a strand wears, the loom's frames for running or resting (index.faces).
import type { Thread } from './types.ts';

export const LIVE = ['working', 'holding', 'idle', 'parked', 'failed'];
export const LOG = 6;

// every thread's newest strand and its state, by strand id, settled threads included: a strand settling still moves
export type Seen = Record<string, { thread: string; state: string }>;

export function seen(threads: Thread[]): Seen {
	const now: Seen = {};
	for (const t of threads) {
		const s = (t.strands || []).slice(-1)[0];
		if (s) now[s.id] = { thread: t.id, state: s.state || '—' };
	}
	return now;
}

// The strands whose state moved since `prev`, and their log lines, newest first. No `prev` (the first load) moves none.
export function moves(
	prev: Seen | null,
	now: Seen,
	at: string
): { changed: Set<string>; lines: string[] } {
	const changed = new Set<string>(),
		lines: string[] = [];
	if (prev)
		for (const [id, n] of Object.entries(now)) {
			const was = prev[id]?.state;
			if (was === n.state) continue;
			changed.add(id);
			lines.unshift(`${at.slice(11, 19)} ${n.thread} ${was || 'new'}→${n.state}`);
		}
	return { changed, lines };
}

export type Faces = Record<string, string[]>;

// a working strand plays the running frames, every other state the resting ones; with no faces, the name
export function face(faces: Faces | undefined, state: string | undefined, tick: number): string {
	const f = faces?.[state === 'working' ? 'running' : 'idle'];
	return f?.length ? f[tick % f.length] : 'brnrd';
}
