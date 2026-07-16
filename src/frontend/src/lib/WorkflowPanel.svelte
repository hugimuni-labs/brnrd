<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import { parsePlan } from './plans';

	interface Props {
		/** Raw markdown of the account-dominion workflow.md (CS8). */
		md: string;
	}

	let { md }: Props = $props();

	// Collapsed by default: preferences are a contract you consult, not a
	// feed you watch — one click opens the whole doc.
	let open = $state(false);
	let openSections = new SvelteSet<string>();

	let parsed = $derived(parsePlan(md));

	function toggleSection(key: string) {
		if (openSections.has(key)) openSections.delete(key);
		else openSections.add(key);
	}
</script>

<div class="subpanel p-2.5 text-xs">
	<button
		class="flex w-full items-center justify-between gap-3 text-left"
		onclick={() => (open = !open)}
		aria-expanded={open}
	>
		<span class="font-medium text-amber-100">
			workflow preferences
			<span class="ml-2 font-mono text-[10px] text-stone-500 uppercase">cs8 · pace &amp; flow</span>
		</span>
		<span class="flex shrink-0 items-center gap-2 font-mono text-stone-500">
			{#if parsed.updatedDate}
				<span>updated {parsed.updatedDate}</span>
			{/if}
			<span class="text-stone-400">{open ? '▾' : '▸'}</span>
		</span>
	</button>

	{#if open}
		<p class="mt-2 border-t border-stone-800/70 pt-2 text-stone-400">
			The standing agreement between you and the resident about how work flows — autonomy scope,
			delivery ceremony, gating, cadence. Edit <span class="font-mono">workflow.md</span> at the
			account-dominion root (or ask the resident); every wake reads it.
		</p>
		{#each parsed.sections as section (section.title)}
			{@const isOpen = openSections.has(section.title)}
			<div class="mt-2 border-t border-stone-800/70 pt-2">
				<button
					class="flex w-full items-center justify-between gap-2 text-left"
					onclick={() => toggleSection(section.title)}
					aria-expanded={isOpen}
				>
					<span class="text-[10px] tracking-wide text-stone-500 uppercase">
						{section.title || 'preamble'}
					</span>
					<span class="font-mono text-stone-600">{isOpen ? '▾' : '▸'}</span>
				</button>
				{#if isOpen}
					<pre class="mt-1 font-sans whitespace-pre-wrap text-stone-300">{section.body}</pre>
				{/if}
			</div>
		{/each}
	{/if}
</div>
