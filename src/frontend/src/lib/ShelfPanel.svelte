<script lang="ts">
	// The shelf, given a place on /daily (maintainer, 2026-09-06: "we have no
	// place for the shelf items on the web UI, no?"). One row per
	// `surface/shelf/*.md` page — title, `made:`, `keeps:` — opening the same
	// corpus renderer the work-surface library already uses in place
	// (`onOpen`, wired to Dashboard's `openInLibrary`). An expired page stays
	// in the list, struck, per the shelf's own rule (surface/shelf/index.md
	// §Expiry is rendered, never swept) — this panel never hides one.
	import type { ShelfEntry } from './shelfPages';

	interface Props {
		entries: ShelfEntry[];
		onOpen?: (path: string) => void;
	}
	let { entries, onOpen = undefined }: Props = $props();
</script>

{#if entries.length === 0}
	<p class="text-sm text-ink-quiet">nothing on the shelf.</p>
{:else}
	<ul class="flex flex-col gap-2">
		{#each entries as entry (entry.path)}
			<li class="border-l-2 pl-2 {entry.expired ? 'border-stone-800/70' : 'border-amber-700/40'}">
				<button
					type="button"
					class="cursor-pointer text-left text-sm hover:text-amber-100 {entry.expired
						? 'text-ink-mute line-through'
						: 'text-stone-200'}"
					onclick={() => onOpen?.(entry.path)}
				>
					{entry.title}
				</button>
				<p class="font-mono text-[10px] text-ink-quiet">
					{#if entry.madeLine}made: {entry.madeLine}{:else}made: undeclared{/if}
				</p>
				<p class="font-mono text-[10px] {entry.expired ? 'text-amber-700/80' : 'text-ink-quiet'}">
					{#if entry.keepsLine}
						{entry.expired ? 'expired · ' : 'keeps: '}{entry.expired
							? entry.keepsLine.replace(/^expired\s*/i, '')
							: entry.keepsLine}
					{:else}
						keeps: undeclared
					{/if}
				</p>
			</li>
		{/each}
	</ul>
{/if}
