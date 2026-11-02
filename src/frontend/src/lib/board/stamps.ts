// The stamps under a letter (board.html stampsHtml): each strand that picked it up, no disposition yet, chatter, a
// channel send's state, one line per wish citing it (its state now, short id, thread; 10010: the history repeated per
// citing letter buried the card, and lives in the dissect panel and the title), and the wish it stamps.
import { hm } from './text.ts';
import type { Letter } from './types.ts';

export type LetterStamp = { cls: string; text: string; title?: string };

export function stamps(l: Letter): LetterStamp[] {
	const out: LetterStamp[] = [];
	for (const s of l.shown || [])
		out.push({ cls: 'read', text: `picked up · ${s.strand.slice(-6)} ${hm(s.at)}` });
	if (l.open)
		out.push({
			cls: 'open',
			text: 'no disposition',
			title: 'no wish say or chatter cites this letter yet'
		});
	if (l.chatter)
		out.push({
			cls: 'heard',
			text: 'chatter',
			title: `cited as wanting nothing by ${l.chatter.join(', ')}`
		});
	if (l.sent)
		out.push({
			cls: l.sent.state === 'sent' ? 'read' : 'heard',
			text: `to channel · ${l.sent.state}`
		});
	const seen = new Set<string>();
	for (const w of l.wishes || []) {
		if (seen.has(w.wish)) continue;
		seen.add(w.wish);
		out.push({
			cls: w.state,
			text: [w.state, w.wish.slice(-6), w.thread].filter(Boolean).join(' · '),
			title: [
				w.line,
				...w.stamps.map((s) => `${s.stamp} ${hm(s.at)}${s.ref ? ' ' + s.ref : ''}`)
			].join('\n')
		});
	}
	if (l.wish) out.push({ cls: 'heard', text: `stamps ${l.wish.slice(-6)}` });
	return out;
}
