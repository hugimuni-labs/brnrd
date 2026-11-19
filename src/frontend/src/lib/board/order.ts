// Column order (board.py board_state names each thread's state): seated by freshest letter, your call by when it
// entered the state (a to-do, not a feed), quiet by when the thread opened (a shelf: a body leaving moves its column
// only out of seated, never within quiet, 4921), settled into one stack at the far right.
import type { BoardState, Letter, Thread } from './types.ts';
import { INBOX } from './sides.ts';

export const SETTLED = '~settled';
const RANK: Record<string, number> = {
	seated: 0,
	'your-call': 1,
	mine: 2,
	waiting: 3,
	quiet: 4,
	settled: 5
};

export const STATE_LABEL = {
	seated: 'Working',
	'your-call': 'Your call',
	waiting: 'Waiting',
	mine: 'Stuck',
	quiet: 'Quiet',
	settled: 'Settled'
} satisfies Record<BoardState, string>;

export const BOARD_STATES = Object.keys(STATE_LABEL) as BoardState[];
export const DEFAULT_STATES: readonly BoardState[] = ['seated', 'your-call', 'waiting', 'mine'];

export function readFilters(saved: string): readonly BoardState[] {
	try {
		const values: unknown = JSON.parse(saved);
		const selected = Array.isArray(values)
			? BOARD_STATES.filter((state) => values.includes(state))
			: [];
		return selected.length ? selected : DEFAULT_STATES;
	} catch {
		return DEFAULT_STATES;
	}
}

export const bst = (t: Thread): BoardState => t.board?.state || 'quiet';
// A thread with work left and no body (4987) sits after the columns that wait on him or on a declared reason.
export const rank = (t: Thread): number =>
	bst(t) === 'mine' && t.board?.stall ? 3.5 : RANK[bst(t)];

export function byThread(letters: Letter[]): Map<string, Letter[]> {
	const by = new Map<string, Letter[]>();
	for (const l of letters)
		for (const t of l.threads || []) (by.get(t) || by.set(t, []).get(t)!).push(l);
	return by;
}

// Freshness is the newest letter; last_at (any fact a strand writes) only for a thread with none.
export const lastAt = (t: Thread, by: Map<string, Letter[]>): string => {
	const ls = by.get(t.id) || [];
	return ls.length ? ls[ls.length - 1].at : t.last_at || '';
};

export function sortThreads(threads: Thread[], by: Map<string, Letter[]>): Thread[] {
	const opened = (t: Thread) => t.opened_at || '';
	return threads
		.filter((t) => t.id !== INBOX)
		.sort(
			(a, b) =>
				rank(a) - rank(b) ||
				(bst(a) === 'your-call'
					? String(a.board?.since).localeCompare(String(b.board?.since))
					: bst(a) === 'quiet'
						? opened(b).localeCompare(opened(a))
						: lastAt(b, by).localeCompare(lastAt(a, by))) ||
				a.id.localeCompare(b.id)
		);
}

// Apply the selection before arranging the columns. The inbox always stands, with the settled stack and its
// opened threads at the end when selected.
export function wanted(
	sorted: Thread[],
	open: Set<string>,
	filters: ReadonlySet<BoardState>
): string[] {
	const visible = sorted.filter((t) => filters.has(bst(t)));
	const settled = visible.filter((t) => bst(t) === 'settled');
	return [
		INBOX,
		...visible.filter((t) => bst(t) !== 'settled').map((t) => t.id),
		...(settled.length ? [SETTLED] : []),
		...settled.filter((t) => open.has(t.id)).map((t) => t.id)
	];
}

export const REORDER = 10000;

// Column order moves at most once per REORDER ms; a new thread takes its sorted place at once. While the bead sheet is
// open, or a thread column is under the pointer (5095), nothing moves: a new thread joins at the end. `have` is the
// order on screen; holding to creation order snapped a column that had just moved back to where it was made (4942).
export type Hold = { first: boolean; held: boolean; now: number; last: number };
export function arrange(
	want: string[],
	have: string[],
	h: Hold
): { ids: string[]; last: number; wait: number | null } {
	have = have.filter((id) => want.includes(id));
	if (!h.first && h.held)
		return { ids: [...have, ...want.filter((id) => !have.includes(id))], last: h.last, wait: null };
	if (h.first || h.now - h.last >= REORDER)
		return { ids: want, last: want.join() !== have.join() ? h.now : h.last, wait: null };
	const ids = have.slice();
	want.forEach((id, i) => {
		if (!ids.includes(id)) ids.splice(i, 0, id);
	});
	return { ids, last: h.last, wait: ids.join() !== want.join() ? h.last + REORDER - h.now : null };
}
