// A thread column's ledger (4920 switch, board-wishes/design.md): who owes the next move decides the section, waits
// on you, in hand, settled. One row per wish; an assumed (derived) wish nests under the said wish it read from: the
// one whose letters (says or reply chain) hold a letter the derived wish's says cite, in the same section. With no
// such wish it stays top-level, still marked, and names the said wish on another thread it came from.
import type { Call, Letter, Step, Thread, Wish } from './types.ts';
import { answered } from './calls.ts';
import { DONE, isDone, STEP5, waits } from './stages.ts';

// A thread's wishes: the ones it was opened for, the ones filed on it, and the ones its letters are said on. A letter
// tied to none of them folds under the thread's one opened-for wish; with several, it stays loose.
export type ThreadWishes = { W: Wish[]; extra: Record<string, string[]>; loose: string[] };
export function threadWishes(t: Thread, cl: Letter[], wishes: Wish[]): ThreadWishes {
	const of = (t.opened_for || []).map((o) => o.wish),
		ids = new Set(cl.map((l) => l.id));
	const W = wishes
		.filter(
			(w) => of.includes(w.wish) || w.thread === t.id || (w.says || []).some((s) => ids.has(s.id))
		)
		.sort(
			(a, b) =>
				+of.includes(b.wish) - +of.includes(a.wish) ||
				+DONE.includes(a.state) - +DONE.includes(b.state) ||
				String(b.at).localeCompare(String(a.at))
		);
	const tied = new Set(W.flatMap((w) => w.letters || [])),
		rest = cl.filter((l) => !tied.has(l.id)).map((l) => l.id),
		home = of.length === 1 && W.find((w) => w.wish === of[0]);
	return { W, extra: home ? { [home.wish]: rest } : {}, loose: home ? [] : rest };
}

export type Section = 'waits' | 'hand' | 'settled';
export const LABEL: Record<Section, string> = {
	waits: 'VERDICT',
	hand: 'IN HAND',
	settled: 'SETTLED'
};

export type LedgerRow = {
	w: Wish;
	sec: Section;
	call?: Call;
	// in hand: working once granting began, else waiting on the world
	hand: '' | 'working' | 'world';
	status: string;
	// the said wish on another thread an assumed one came from
	from?: string;
	kids: LedgerRow[];
	steps: (Step & { i: number })[];
	extra: string[];
};

export type Ledger = {
	many: boolean;
	balance: { said: number; derived: number; delivered: number; owe: number };
	sections: { k: Section; rows: LedgerRow[]; n: number }[];
	unplanned: (Step & { i: number })[];
	active: (Step & { i: number }) | undefined;
	loose: string[];
};

export function ledger(t: Thread, tw: ThreadWishes, wishes: Wish[], pending: Letter[]): Ledger {
	const calls = t.board?.calls || [],
		asks = new Set(calls.filter((c) => c.kind === 'ask' && !c.closed).map((c) => c.wish));
	const W = [...tw.W];
	for (const c of calls)
		if (!c.closed && !W.some((w) => w.wish === c.wish)) {
			const w = wishes.find((w) => w.wish === c.wish);
			if (w) W.push(w);
		}
	const shown = new Set(W.map((w) => w.wish)),
		st = (t.steps || []).map((s, i) => ({ ...s, i })),
		sx = new Map<string, (Step & { i: number })[]>(),
		unplanned: (Step & { i: number })[] = [];
	// Steps (6lozd 1): each Plan row under the first wish it cites that this column shows.
	for (const s of st) {
		const h = s.wishes.find((id) => shown.has(id));
		if (h) (sx.get(h) || sx.set(h, []).get(h)!).push(s);
		else unplanned.push(s);
	}
	const many = W.length > 1;
	const ans = new Set(calls.filter((c) => answered(c, pending)).map((c) => c.wish));
	const sec = (w: Wish): Section =>
		waits(w, asks) && !ans.has(w.wish) ? 'waits' : isDone(w) ? 'settled' : 'hand';
	const said = W.filter((w) => w.origin !== 'derived'),
		kids = new Map<string, Wish[]>(said.map((w) => [w.wish, []])),
		top: Wish[] = [];
	const reachOf = (w: Wish) => new Set([...(w.letters || []), ...(w.says || []).map((x) => x.id)]);
	const reach = new Map(said.map((w) => [w.wish, reachOf(w)]));
	const away = (w: Wish) => {
		const ids = (w.says || []).map((s) => s.id);
		if (w.origin !== 'derived') return undefined;
		return wishes.find(
			(s) => s.origin !== 'derived' && !kids.has(s.wish) && ids.some((id) => reachOf(s).has(id))
		)?.wish;
	};
	for (const w of W) {
		if (w.origin !== 'derived') {
			top.push(w);
			continue;
		}
		const ids = (w.says || []).map((s) => s.id),
			p = said.find((s) => sec(s) === sec(w) && ids.some((id) => reach.get(s.wish)!.has(id)));
		if (p) kids.get(p.wish)!.push(w);
		else top.push(w);
	}
	const row = (w: Wish, k: Section, extra: string[], nested: boolean): LedgerRow => {
		const call = calls.find((c) => c.wish === w.wish),
			a = call && answered(call, pending);
		const own = w.state && !(STEP5 as readonly string[]).includes(w.state) ? w.state : '';
		const status =
			k === 'waits'
				? call?.kind === 'ask'
					? 'asks you'
					: 'verdict'
				: a
					? a.pending
						? 'sending'
						: 'answered'
					: k === 'settled'
						? w.verdict?.as || own
						: own;
		return {
			w,
			sec: k,
			call,
			hand:
				k === 'hand'
					? w.stamps.some((s) => s.stamp === 'granting' || s.stamp === 'result')
						? 'working'
						: 'world'
					: '',
			status,
			from: nested ? undefined : away(w),
			kids: nested ? [] : (kids.get(w.wish) || []).map((x) => row(x, k, [], true)),
			steps: sx.get(w.wish) || [],
			extra
		};
	};
	const sections: Ledger['sections'] = [];
	for (const k of ['waits', 'hand', 'settled'] as Section[]) {
		const rs = top.filter((w) => sec(w) === k);
		if (!rs.length && (k !== 'waits' || !many)) continue;
		const rows = rs.map((w) => row(w, k, tw.extra[w.wish] || [], false));
		sections.push({ k, rows, n: rows.length + rows.reduce((a, r) => a + r.kids.length, 0) });
	}
	return {
		many,
		balance: {
			said: said.length,
			derived: W.length - said.length,
			delivered: W.filter(
				(w) => w.state === 'delivered' || (w.results || []).some((r) => r.delivered)
			).length,
			owe: W.filter((w) => sec(w) === 'waits').length
		},
		sections,
		unplanned,
		active: st.find((s) => !s.receipt),
		loose: tw.loose
	};
}
