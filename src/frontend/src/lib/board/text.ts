// Small readers board.html shared across every surface: colour, times, ids, a letter's quote and refs.
import type { Letter } from './types.ts';

export const hue = (id: string): string => {
	let h = 0;
	for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) % 360;
	return `hsl(${h} 60% 40%)`;
};

// '2026-10-27T10:21:00Z' → '10-27 10:21'
export const hm = (at?: string | null): string => (at ? at.slice(5, 16).replace('T', ' ') : '');

export function dur(s: number): string {
	s = Math.max(0, Math.round(s));
	if (s < 60) return `${s}s`;
	if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
	return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
}

export const secs = (a: string, b: string): number => (Date.parse(b) - Date.parse(a)) / 1000;
export const short = (id: string): string => String(id).split('/').pop()!.split(':').pop()!;
export const byAt = <T extends { at: string; id: string }>(a: T, b: T): number =>
	a.at.localeCompare(b.at) || a.id.localeCompare(b.id);

// A Plan row's text without its markdown links and backticks.
export const stepText = (t: string): string =>
	String(t)
		.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
		.replace(/`/g, '');

// A Telegram reply's quote arrives as a leading '> ' block up to the first blank line; or the letter it answers, by re.
export function splitQuote(l: Letter, byId: Map<string, Letter>): [string | null, string] {
	const b = String(l.body);
	if (b.startsWith('> ')) {
		const i = b.indexOf('\n\n');
		if (i > 0) return [b.slice(2, i), b.slice(i + 2)];
	}
	if (l.person && l.re) {
		const q = byId.get(l.re) || byId.get('letter:' + l.re);
		if (q && q.id !== l.id) return [q.body, b];
	}
	return [null, b];
}

// &refs, as refs.py reads them: '&' at the start or after a space or an opening bracket, ending on a word character, outside code.
const REF = /(?<![^\s([])&([A-Za-z0-9][\w:/#.-]*\w|[A-Za-z0-9])/g;
export const refTokens = (b: string): string[] => [
	...new Set(
		[
			...String(b || '')
				.replace(/```[\s\S]*?```|`[^`\n]*`/g, '')
				.matchAll(REF)
		].map((m) => m[1])
	)
];

// A bare id is a chip too (wejeb): a wish, a letter, a strand or strand/letter, a board say, a thread. Only these
// shapes, so a plain word never becomes one; a short id ('4954', 'wejeb') still needs its '&'.
const BARE = String.raw`w-[a-z0-9]{12}|letter:[a-z]+:\d+:[\w-]*\w|s-[a-z0-9]{4}-[a-z0-9]{6}(?:\/[a-z0-9]{5})?|board-[a-z0-9]{6,}|thread:[a-z0-9][\w-]*\w`;
const CHIP = new RegExp(
	String.raw`(^|[\s(\[])&([A-Za-z0-9][\w:\/#.-]*\w|[A-Za-z0-9])|(?<![\w:\/.-])(${BARE})(?![\w\/-])`,
	'g'
);

// A text cut into plain runs and chips; a chip names the bead it opens. board.html did this on escaped HTML;
// here the component renders the runs, so nothing is ever injected.
export type Run = { text: string } | { text: string; bead: string; amp: boolean };
export function chips(text: string): Run[] {
	const s = String(text ?? ''),
		out: Run[] = [];
	let last = 0;
	for (const m of s.matchAll(CHIP)) {
		const [all, pre, tok, bare] = m;
		const at = m.index! + (bare ? 0 : pre.length);
		if (at > last) out.push({ text: s.slice(last, at) });
		out.push(
			bare ? { text: bare, bead: bare, amp: false } : { text: '&' + tok, bead: tok, amp: true }
		);
		last = m.index! + all.length;
	}
	if (last < s.length) out.push({ text: s.slice(last) });
	return out;
}
