// One stage map per wish, read by the pips and the bead panel alike (m435g): five stages of the wish's life, heard ·
// read · granting · delivered · agreed (assumed is an origin, the row's tag says it). Filled = happened, hollow = not
// yet, the current stage amber, a ring on the stage that waits on him; a dropped or corrected verdict marks agreed.
import type { Stamp, Wish } from './types.ts';
import { hm } from './text.ts';

export const STEP5 = ['heard', 'read', 'granting', 'delivered', 'agreed'] as const;
export type Stage = (typeof STEP5)[number];
export const DONE = ['delivered', 'accepted', 'dropped', 'declined'];

export const stageOf = (s: Stamp): string =>
	s.stamp === 'result' || s.stamp === 'scoped'
		? 'granting'
		: s.stamp === 'accepted'
			? 'agreed'
			: s.stamp;
const isStage = (k: string): k is Stage => (STEP5 as readonly string[]).includes(k);

export const isDone = (w: Wish): boolean =>
	!!w.verdict || ['accepted', 'dropped', 'declined'].includes(w.state);
export const waits = (w: Wish, asks: Set<string>): boolean =>
	!isDone(w) && (w.state === 'delivered' || asks.has(w.wish));

export const verdictMark = (w: Wish): '' | 'x' | 'c' => {
	const v = w.verdict?.as || (w.state === 'dropped' || w.state === 'declined' ? w.state : '');
	return /drop|declin/.test(v) ? 'x' : /correct/.test(v) ? 'c' : '';
};

export function pipOn(w: Wish): Set<Stage> {
	const on = new Set<Stage>(w.stamps.map(stageOf).filter(isStage));
	if (on.has('delivered')) on.add('granting');
	if (w.verdict?.as || w.state === 'accepted') on.add('agreed');
	// A later stage means the earlier ones happened: most granted wishes predate read stamps. Filled, said 'implied'.
	// A dropped or corrected verdict implies nothing before it.
	const last = Math.max(
		...[...on].filter((k) => k !== 'agreed' || !verdictMark(w)).map((k) => STEP5.indexOf(k))
	);
	STEP5.slice(0, last).forEach((k) => on.add(k));
	return on;
}

export type StageRow = {
	k: Stage;
	on: boolean;
	cur: boolean;
	wait: boolean;
	mark: '' | 'x' | 'c';
	at?: string;
};

// `answered`: his verdict letter for this wish is in, or pending here.
export function stages(w: Wish, asks: Set<string>, answered = false): StageRow[] {
	const on = pipOn(w),
		at = new Map<Stage, string>();
	for (const s of w.stamps) {
		const k = stageOf(s);
		if (isStage(k) && !at.has(k)) at.set(k, s.at);
	}
	if (w.verdict && !at.has('agreed')) at.set('agreed', w.verdict.at);
	if (on.has('granting') && !at.has('granting') && at.has('delivered'))
		at.set('granting', at.get('delivered')!);
	// times never run backwards: an earlier stage stamped after a later one takes the later one's time
	for (let i = STEP5.length - 2; i >= 0; i--) {
		const a = at.get(STEP5[i]),
			b = STEP5.slice(i + 1)
				.map((k) => at.get(k))
				.find(Boolean);
		if (a && b && a > b) at.set(STEP5[i], b);
	}
	const last = [...STEP5].reverse().find((k) => on.has(k)),
		done = isDone(w),
		wait = waits(w, asks) && !answered ? STEP5.find((k) => !on.has(k)) : null,
		mark = verdictMark(w);
	return STEP5.map((k) => ({
		k,
		on: on.has(k),
		cur: !done && k === last,
		wait: k === wait,
		mark: k === 'agreed' ? mark : '',
		at: at.get(k)
	}));
}

export const stageText = (s: StageRow): string =>
	s.k +
	(s.mark === 'x' ? ' (dropped)' : s.mark === 'c' ? ' (corrected)' : '') +
	(s.at
		? ' ' + hm(s.at)
		: s.on
			? ' (implied by a later stage, no stamp)'
			: s.wait
				? ' (verdict)'
				: ' (not yet)');

export const stageWord = (w: Wish): string => {
	const m = verdictMark(w);
	if (m) return m === 'x' ? 'dropped' : 'corrected';
	const on = pipOn(w);
	return [...STEP5].reverse().find((k) => on.has(k)) || 'new';
};
