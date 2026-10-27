<script lang="ts">
	// A fold of calls between two letters is an episode (a bar: amber running, ice thinking, ember failed, acid reached
	// outside); a change of `say` starts a beat with the thinking time before it; a beat opens to its calls.
	import { bar, beats, reach, what, type Fold } from '../tape.ts';
	import { dur, secs } from '../text.ts';
	import { board } from './board.svelte.ts';
	let { f }: { f: Fold } = $props();
	const b = board();
	const calls = $derived(f.fold);
	const on = $derived(b.open.has('e:' + f.id));
	const bs = $derived(beats(calls));
	const think = $derived(bs.reduce((a, x) => a + (x.thought || 0), 0));
	const last = $derived([...calls].reverse().find((c) => c.say)?.say || what(calls[calls.length - 1]));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div
	class="tool tape"
	class:on
	data-k={f.id}
	data-strand={f.strand}
	onclick={() => {
		if (!on) b.openBead(f.strand, 'keep');
		b.toggle(b.open, 'e:' + f.id);
	}}
>
	<div class="ep-h">
		<span class="t">{calls[0].at.slice(11, 16)}</span><span
			>{on ? '▾' : '▸'} {calls.length} call{calls.length > 1 ? 's' : ''} · {dur(
				secs(calls[0].at, calls[calls.length - 1].end!)
			)}{think ? ` · thought ${dur(think)}` : ''}</span
		><span class="s">{last}</span>
	</div>
	<div class="bar">{#each bar(calls) as s, i (i)}<b class={s.kind} style:flex-grow={s.grow}></b>{/each}</div>
	{#if on}
		{#each bs as bt, i (i)}
			{@const k = 'b:' + f.id + '|' + i}
			<div class="beat">
				<div
					class="beat-h"
					onclick={(e) => {
						e.stopPropagation();
						b.toggle(b.open, k);
					}}
				>
					{#if bt.thought != null}<span class="th">thought {dur(bt.thought)}</span>{/if}
					{#if bt.say}<q>{bt.say}</q>{:else}<span class="dim">{what(bt.calls[0])}</span>{/if}
					<span class="n"
						>{b.open.has(k) ? '▾' : '▸'} {bt.calls.length} call{bt.calls.length > 1 ? 's' : ''} · {dur(
							secs(bt.calls[0].at, bt.calls[bt.calls.length - 1].end!)
						)}</span
					>
				</div>
				{#if b.open.has(k)}{#each bt.calls as c, j (j)}<div class="callrow" class:x={c.failed} title={c.cmd || ''}>
							{#if reach(c)}<span class="o">outside</span>{/if}{c.at.slice(11, 19)}
							{c.by ? '↳ ' : ''}{what(c)} · {dur(secs(c.at, c.end!))}{c.failed ? ' ✗' : ''}
						</div>{/each}{/if}
			</div>
		{/each}
	{/if}
</div>
