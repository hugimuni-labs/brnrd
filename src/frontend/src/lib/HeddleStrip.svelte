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
		? 'filtering — press a lit rune to add, a dim one to clear'
		: 'lens · press a rune to filter'}
>
	<span aria-hidden="true">◒</span>
	{litCount}/{threads.length}
	{filtered ? 'lit' : 'all lit'}
</span>
<!-- Every topic's rune, lit or dim, each a working toggle — the Photoshop
     layer eyes at their smallest. A lit rune also wears a bottom ring so
     the on/off read survives two topics landing on close hues. -->
<span class="ml-auto flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[16px]">
	{#each threads as thread (thread.canonicalId)}
		{@const lit = isLit(thread.canonicalId)}
		<button
			type="button"
			class="cursor-pointer rounded-sm leading-none"
			style={lit
				? `color: ${thread.face.color}; box-shadow: 0 1.5px 0 0 ${thread.face.color};`
				: ''}
			class:text-ink-mute={!lit}
			class:opacity-40={!lit}
			aria-pressed={lit}
			title={`${thread.title} · ${lit ? 'lit — filtering it in' : 'off — press to filter to it'}`}
			onclick={() => onToggle?.(thread.canonicalId)}
		>
			{thread.face.glyph}{#if weaving.has(thread.canonicalId)}<span
					class="text-amber-300/90"
					aria-label="weaving now">↯</span
				>{/if}
		</button>
	{/each}
	{#if filtered}
		<button
			type="button"
			class="cursor-pointer font-mono text-[9px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			onclick={() => onAll?.()}
		>
			all
		</button>
	{/if}
</span>
