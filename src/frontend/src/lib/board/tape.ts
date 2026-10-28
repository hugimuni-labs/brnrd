// A thread's finished calls fold into one row between letters; a working strand's open call (or its thinking gap) is
// a live block that ticks. The block sits at the open call's start (or the last end): letters after it reached the
// body at no tool boundary yet.
import type { Letter, Live, Strand, Thread, ToolCall } from './types.ts';
import { secs } from './text.ts';

export type LiveLine = {
	verb: string;
	since: string;
	abs?: boolean;
	open: ToolCall[];
	say?: string;
};

export const what = (c: ToolCall): string =>
	`${c.tool}${c.arg || c.cmd ? ' · ' + (c.arg || c.cmd) : ''}`;
// A call that reached outside the machine.
export const reach = (c: ToolCall): boolean =>
	/\b(publish|gh (pr|api)|git push)\b/.test(c.cmd || c.arg || '');

export function liveOf(s: Strand, live: Record<string, Live>): LiveLine | null {
	const v = live[s.id];
	if (!v) return null;
	const working = s.state === 'working';
	if (v.paused)
		return { verb: 'paused since', since: v.paused_at!, abs: true, open: v.open, say: v.say };
	if (v.open.length)
		return {
			verb: working ? 'running' : 'cut off',
			since: v.open[0].at,
			abs: !working,
			open: v.open,
			say: v.say
		};
	if (working && v.last_end) return { verb: 'thinking', since: v.last_end, open: [], say: v.say };
	return null;
}

export type Fold = { id: string; fold: ToolCall[]; tool: true; strand: string; at: string };
export type Row = Letter | Fold | { id: string; live: LiveLine; strand: string; at: string };

// A thread's letters with its strands' finished calls between them, consecutive calls of one strand folded into one.
export function withTools(
	ls: Letter[],
	t: Thread,
	tools: Record<string, ToolCall[]>,
	live: Record<string, Live>
): Row[] {
	const out: (Row | { id: string; call: ToolCall; strand: string; at: string })[] = [...ls];
	for (const s of t.strands || []) {
		for (const r of (tools[s.id] || []).filter((r) => r.end))
			out.push({ id: `tool:${s.id}:${r.at}`, call: r, strand: s.id, at: r.at });
		const lv = liveOf(s, live);
		if (lv) out.push({ id: `live:${s.id}`, live: lv, strand: s.id, at: lv.since });
	}
	out.sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id));
	const folded: Row[] = [];
	for (const l of out) {
		const f = folded[folded.length - 1];
		if ('call' in l) {
			if (f && 'fold' in f && f.strand === l.strand) f.fold.push(l.call);
			else
				folded.push({
					id: `fold:${l.strand}:${l.at}`,
					fold: [l.call],
					tool: true,
					strand: l.strand,
					at: l.at
				});
		} else folded.push(l);
	}
	return folded;
}

// A change of `say` starts a beat, with the thinking time before it; a beat opens to its calls.
export type Beat = { say: string; calls: ToolCall[]; thought: number | null };
export function beats(f: ToolCall[]): Beat[] {
	const out: Beat[] = [];
	let prev: ToolCall | null = null;
	for (const c of f) {
		if (!out.length || (c.say && c.say !== prev?.say))
			out.push({ say: c.say || '', calls: [], thought: prev ? secs(prev.end!, c.at) : null });
		out[out.length - 1].calls.push(c);
		prev = c;
	}
	return out;
}

// The episode bar: amber running, ice thinking, ember failed, acid reached outside; each segment's grow is its seconds.
export type Seg = { kind: 'r' | 'th' | 'x' | 'o'; grow: number };
export function bar(f: ToolCall[]): Seg[] {
	const out: Seg[] = [];
	f.forEach((c, i) => {
		if (i) {
			const g = secs(f[i - 1].end!, c.at);
			if (g > 0) out.push({ kind: 'th', grow: g });
		}
		out.push({
			kind: c.failed ? 'x' : reach(c) ? 'o' : 'r',
			grow: Math.max(0.2, secs(c.at, c.end!))
		});
	});
	return out;
}
