// The stamps under a letter (board.html stampsHtml): each strand that picked it up, no disposition yet, chatter, a
// channel send's state, every stamp of each wish citing it, and the wish it stamps.
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
	for (const w of l.wishes || [])
		for (const s of w.stamps)
			out.push({
				cls: s.stamp,
				text: `${s.stamp} · ${w.wish.slice(-6)} ${hm(s.at)}`,
				title: w.line + (s.ref ? '\n' + s.ref : '')
			});
	if (l.wish) out.push({ cls: 'heard', text: `stamps ${l.wish.slice(-6)}` });
	return out;
}
