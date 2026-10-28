// A call is an open item whose ref is a wish id (board.py calls_of): an ask, or a delivered wish. Both take done/agree
// in one click or correct in the composer, sent as his letter tied to the wish. It collapses once that letter is in
// the ledger (c.closed), or while it is still pending here; nothing else is stored, so a reload shows the same.
import type { Call, Letter, Thread } from './types.ts';

export type Answer = { verdict: string; at?: string; pending: boolean };

export function answered(c: Call, pending: Letter[]): Answer | null {
	if (c.closed) return { verdict: c.closed.verdict, at: c.closed.at, pending: false };
	const p = pending.find((l) => l.wish === c.wish && l.verdict);
	return p ? { verdict: p.verdict!, pending: true } : null;
}

export const allCalls = (threads: Thread[]): Call[] => threads.flatMap((t) => t.board?.calls || []);
export const openAsks = (calls: Call[]): Set<string> =>
	new Set(calls.filter((c) => c.kind === 'ask' && !c.closed).map((c) => c.wish));

// The inbox keeps its stack, but only an open ask or a card he opened stands at full height; the rest (unopened
// verdicts, answered cards) fold into one counted line that unfolds the whole stack (ug2om 1: 12 cards in a 34% box
// hid the pane).
export type Stack = { shown: Call[]; fold: { verdicts: number; answered: number } | null };
export function stack(calls: Call[], pending: Letter[], open: Set<string>, unfold: boolean): Stack {
	const stand = (c: Call) => !answered(c, pending) && (c.kind === 'ask' || open.has(c.wish));
	const folded = calls.filter((c) => !stand(c));
	const verdicts = folded.filter((c) => !answered(c, pending)).length;
	return {
		shown: unfold || folded.length < 2 ? calls : calls.filter(stand),
		fold: folded.length < 2 ? null : { verdicts, answered: folded.length - verdicts }
	};
}

// The letter a press sends. A done on an ask goes as agree: the intake accepts only a delivered wish.
export const verdictLetter = (act: 'done' | 'agree', wish: string) => ({
	body: `${act} &${wish}`,
	wish,
	verdict: 'agree'
});
// A correct click arms the composer; the letter carries verdict=correct while the &ref is still in it.
export const correctDraft = (wish: string) => `Not quite on &${wish}, I think: `;
export const armedVerdict = (armed: string | null, body: string) =>
	armed && body.includes('&' + armed) ? { wish: armed, verdict: 'correct' } : {};
