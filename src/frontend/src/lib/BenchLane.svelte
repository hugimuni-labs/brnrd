<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import type { BenchFile } from './bench';
	import { benchAgeLabel, benchFileHref } from './bench';

	interface Props {
		files: BenchFile[];
		error?: string | null;
	}

	let { files, error = null }: Props = $props();
</script>

<!-- design-the-loom.md §6/§18 — the bench: folds and inspections at a place,
     one row per file, newest first (the fetch already sorts). Tapping a row
     opens its rendered page at the same address the file's URL is: one more
     mounted-only-when-something-to-show panel, same contract NewsLane and
     ConfigRequests already keep — no "nothing folded yet" sentence for the
     common case of an empty bench. -->
<div class="panel mt-2 p-4" aria-label="the bench">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-sky-200 uppercase">bench</span>
	</div>
	{#if error}
		<p class="text-sm text-red-400">{error}</p>
	{:else}
		<ul class="space-y-1.5">
			{#each files as file (file.path)}
				<li
					class="subpanel"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<a
						href={benchFileHref(file.repo, file.place, file.commit)}
						class="block px-2.5 py-2 text-xs hover:bg-stone-800/40"
					>
						<div class="flex items-center justify-between gap-3">
							<span class="min-w-0 flex-1 truncate font-mono text-stone-300">{file.place}</span>
							<span class="shrink-0 font-mono text-ink-quiet">{file.commit.slice(0, 12)}</span>
							<span class="shrink-0 font-mono text-ink-quiet uppercase tracking-wide"
								>{benchAgeLabel(file.made_at)}</span
							>
						</div>
						{#if file.question}
							<div class="mt-0.5 truncate text-sky-100">{file.question}</div>
						{/if}
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>
