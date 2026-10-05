<script lang="ts">
	import type { TopicThread } from './warpGraph';

	// The heddles' collapsed strip — the lens chip, every rune a working
	// toggle, the `all` reset. Extracted 2026-08-12 (the heddles join the
	// sticky stack) out of HeddleRail's own head row so a second, docked
	// rendering never forks the control: both HeddleRail (its own home,
	// beside the open/close disclosure) and the page's sticky-stack copy
	// call this component over the SAME `selected`/`onToggle`/`onAll`, one
	// `heddleSelection`, two places to reach it from — never a copy-pasted
	// second strip that could drift from the first.

	interface Props {
		threads: TopicThread[];
		/** Canonical ids currently lit; null = all lit (the default). */
		selected: ReadonlySet<string> | null;
		/** Topics with an item held by a live run — wear the weaving bolt. */
		weaving?: ReadonlySet<string>;
		onToggle?: (canonicalId: string) => void;
		onAll?: () => void;
	}

	let {
		threads,
		selected,
		weaving = new Set<string>(),
		onToggle = undefined,
		onAll = undefined
	}: Props = $props();

	function isLit(id: string): boolean {
		return selected === null || selected.has(id);
	}

	let litCount = $derived(selected === null ? threads.length : selected.size);
	let filtered = $derived(selected !== null && selected.size < threads.length);

	// THE RUNES THAT SHIFT (his 2026-08-12 report: "the heddle runes shift
	// when pressed, because the ALL is added"). Two widths moved on the same
	// press: the chip's own wording ("6/6 all lit" → "1/6 lit") pushed the
	// `ml-auto` rune cluster's start, and the `all` reset button's
	// `{#if filtered}` mount added a whole element after the runes. Fixed by
	// reserving both boxes rather than conditioning their presence: the chip
	// drops the "all " word entirely (the numbers already say N of M; a
	// disagreeing wording added nothing "N/M lit" doesn't), and `all` always
	// renders, `invisible` (not unmounted) when there is nothing to reset —
	// `visibility: hidden` keeps its layout box in the flow, `display: none`
	// (an `{#if}`) would not. Positions must not move on press.
</script>

<!-- The rail's own filter chip (his 2026-08-11 read: "it doesn't really
     look that much like filtering") — names the control as a lens even at
     rest, and turns the same amber the WarpGraphView/Cloth "N of M …
     lensed by the heddles" lines wear the moment a press actually narrows
     them: one state, one color, three places (now four, with the docked
     copy), so cause and effect read as the same fact. -->
<span
	class="flex items-center gap-x-1 font-mono text-[10px] tracking-wide uppercase"
	class:text-amber-300={filtered}
	class:text-ink-quiet={!filtered}
	title={filtered
		? 'showing some topics. press one to change which show.'
		: 'topics. press one to show only that topic.'}
>
	{litCount} of {threads.length}
</span>
<!-- Every topic by name. Color is the on/off mark; the name is the control.
     Width stays put on press: every topic stays mounted, and `all` below
     stays mounted too (`invisible` when there is nothing to reset). -->
<span class="flex flex-wrap items-center gap-x-2 gap-y-1">
	{#each threads as thread (thread.canonicalId)}
		{@const lit = isLit(thread.canonicalId)}
		{@const isWeaving = weaving.has(thread.canonicalId)}
		<button
			type="button"
			class="cursor-pointer rounded-sm font-mono text-[11px] leading-none"
			style={`${lit ? `color: ${thread.face.color}; box-shadow: 0 1.5px 0 0 ${thread.face.color};` : ''}${isWeaving ? ` color: ${thread.face.color}; text-shadow: 0 0 5px ${thread.face.color}, 0 0 10px ${thread.face.color};` : ''}`}
			class:text-ink-mute={!lit && !isWeaving}
			class:opacity-40={!lit && !isWeaving}
			class:heddle-weaving={isWeaving}
			aria-pressed={lit}
			aria-label={isWeaving ? `${thread.title}, running now` : thread.title}
			title={isWeaving
				? `${thread.title} · running now`
				: lit
					? `${thread.title} · showing`
					: `${thread.title} · hidden — press to show it`}
			onclick={() => onToggle?.(thread.canonicalId)}
		>
			{thread.title}
		</button>
	{/each}
	<button
		type="button"
		class="cursor-pointer font-mono text-[9px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
		class:invisible={!filtered}
		disabled={!filtered}
		onclick={() => onAll?.()}
	>
		all
	</button>
</span>
