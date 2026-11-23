// A pull request opens this page on one wish. The chain is the ancestors up to
// the person's letter: the board's bead map when the loopback shipped it, and
// the snapshot's says when the page is the cloud dashboard.

export type ChainStep = {
	id: string;
	kind: 'letter' | 'wish' | 'other';
	from: string | null;
	at: string | null;
	line: string;
	person: boolean;
};

export type ShownWish = {
	id: string;
	line: string;
	state?: string;
	chain: ChainStep[];
};

type BeadRow = readonly [string | null, string | null, string | null, string | null, string];

const STRAND = /^s-[a-z0-9]{4}-[a-z0-9]{6}$/;

function kindOf(id: string): ChainStep['kind'] {
	if (id.startsWith('w-')) return 'wish';
	if (id.startsWith('thread:') || STRAND.test(id)) return 'other';
	return 'letter';
}

function personStep(id: string, row: BeadRow): boolean {
	if (kindOf(id) !== 'letter') return false;
	return row[1] === 'person' || (row[2] ?? '').startsWith('person:');
}

/** Ancestors of `wishId`, the person's letter first. Stops at that letter.
 * The wish itself is not in the list: it is the selected row. */
export function chainToRequest(beads: Record<string, BeadRow>, wishId: string): ChainStep[] {
	const steps: ChainStep[] = [];
	const seen = new Set<string>([wishId]);
	let parent = beads[wishId]?.[0] ?? null;
	while (parent && !seen.has(parent)) {
		seen.add(parent);
		const row = beads[parent];
		if (!row) break;
		const person = personStep(parent, row);
		steps.push({
			id: parent,
			kind: kindOf(parent),
			from: row[2],
			at: row[3],
			line: row[4] ?? '',
			person
		});
		if (person) break;
		parent = row[0];
	}
	return steps.reverse();
}

/** Snapshot says, the person's letter first. The head is what the cloud ships. */
export function chainFromSays(
	says: { id: string; from?: string; at?: string; head?: string }[]
): ChainStep[] {
	const steps = says.map((say) => ({
		id: say.id,
		kind: 'letter' as const,
		from: say.from ?? null,
		at: say.at ?? null,
		line: say.head ?? '',
		person: (say.from ?? '').startsWith('person:')
	}));
	return [...steps.filter((step) => step.person), ...steps.filter((step) => !step.person)];
}

export function wishesFromSnapshot(
	wishes: {
		id: string;
		thread?: string;
		line?: string;
		state?: string;
		says?: { id: string; from?: string; at?: string; head?: string }[];
	}[],
	thread: string
): ShownWish[] {
	return wishes
		.filter((wish) => wish.thread === thread)
		.map((wish) => ({
			id: wish.id,
			line: wish.line ?? '',
			state: wish.state,
			chain: chainFromSays(wish.says ?? [])
		}));
}

export function wishesFromBoard(part: {
	wishes?: { wish: string; line?: string; state?: string }[];
	chain?: Record<string, BeadRow>;
	letters?: { id: string; body?: string; from?: string; at?: string }[];
}): ShownWish[] {
	const letters = new Map((part.letters ?? []).map((letter) => [letter.id, letter]));
	return (part.wishes ?? []).map((wish) => ({
		id: wish.wish,
		line: wish.line ?? '',
		state: wish.state,
		chain: chainToRequest(part.chain ?? {}, wish.wish).map((step) => {
			const letter = letters.get(step.id);
			if (!letter) return step;
			return {
				...step,
				from: letter.from ?? step.from,
				at: letter.at ?? step.at,
				line: letter.body || step.line
			};
		})
	}));
}

/** `query` selects a wish on this thread. Anything else is a one-line note, not a selection. */
export function pickWish(
	wishes: ShownWish[],
	query: string | null
): { selected: ShownWish | null; missing: string | null } {
	const asked = (query ?? '').trim();
	if (!asked) return { selected: null, missing: null };
	const found = wishes.find((wish) => wish.id === asked) ?? null;
	return { selected: found, missing: found ? null : asked };
}

/** The selected wish leads, so it is on screen before the scroll. */
export function showOrder(wishes: ShownWish[], selectedId: string | null): ShownWish[] {
	if (!selectedId) return wishes;
	return [...wishes].sort((a, b) => Number(b.id === selectedId) - Number(a.id === selectedId));
}

/** Sign-in returns to this page, query included. The path is same-origin. */
export function signInHref(next: string): string {
	const path = next.startsWith('/') && !next.startsWith('//') ? next : '/dashboard';
	return `/login?next=${encodeURIComponent(path)}`;
}
