<script lang="ts">
	// The home board, top to bottom: who you are talking to, the fuel you
	// have, the threads (the main surface), the log, and the composer. One
	// snapshot drives all of it; this component only arranges it.
	import { resolve } from '$app/paths';
	import LoomComposer from './LoomComposer.svelte';
	import LoomFuelStrip from './LoomFuelStrip.svelte';
	import LoomLog from './LoomLog.svelte';
	import LoomThreadCard from './LoomThreadCard.svelte';
	import {
		FILTERS,
		filterCounts,
		matchesFilter,
		snapshotAge,
		sortThreads,
		type LoomSnapshot,
		type ThreadFilter
	} from './loomSnapshot.ts';

	let {
		snapshot,
		seenAt,
		now,
		body = $bindable()
	}: { snapshot: LoomSnapshot; seenAt: string | null; now: number; body: string | null } = $props();

	let filter = $state<ThreadFilter>('all');
	let threads = $derived(sortThreads(snapshot.threads ?? []));
	let counts = $derived(filterCounts(threads));
	let shown = $derived(threads.filter((t) => matchesFilter(t, filter)));
	let presence = $derived(snapshot.presence);
	let age = $derived(snapshotAge(snapshot.stale_s));
	let live = $derived(presence?.state === 'working' || presence?.state === 'holding');
</script>

<main class="mx-auto max-w-4xl px-5 pb-16 sm:px-6" data-testid="loom-board">
	<header class="flex items-center justify-between pt-4 pb-3">
		<p class="eyebrow eyebrow--asis">brnrd · the loom</p>
		<a href={resolve('/bridge')} class="font-mono text-[10px] text-ink-quiet hover:text-amber-200"
			>the bridge →</a
		>
	</header>

	{#if presence}
		<section class="panel ignite mb-3 p-4" style="--ignite-delay: 40ms" data-testid="presence">
			<div class="flex items-start gap-3">
				<span class="relative mt-1.5 flex size-3 shrink-0" aria-hidden="true">
					{#if presence.state === 'working'}
						<span class="absolute inset-0 rounded-full bg-sky-300/50 motion-safe:animate-ping"
						></span>
					{/if}
					<span
						class={`relative size-3 rounded-full ${live ? 'bg-sky-300 shadow-[0_0_10px_rgba(125,211,252,0.6)]' : presence.state === 'parked' ? 'bg-orange-400' : 'bg-stone-600'}`}
					></span>
				</span>
				<div class="min-w-0 flex-1">
					<p class="flex flex-wrap items-baseline gap-x-2 font-mono text-[11px]">
						<span class="text-stone-100">{presence.thread ?? 'inbox'}</span>
						{#if presence.state}<span class="text-sky-300/90">{presence.state}</span>{/if}
						{#if presence.body}<span class="text-ink-quiet">{presence.body}</span>{/if}
					</p>
					{#if presence.now}
						<p class="mt-1 text-[15px] leading-snug text-stone-200">{presence.now}</p>
					{/if}
					<p class="mt-1.5 flex flex-wrap gap-x-3 font-mono text-[10px] text-ink-mute">
						{#if presence.owed !== undefined}
							<span class={presence.owed > 0 ? 'text-amber-300/90' : ''}>
								owes {presence.owed} letter{presence.owed === 1 ? '' : 's'}
							</span>
						{/if}
						{#if presence.strand}<span>{presence.strand}</span>{/if}
						{#if age}<span class="text-orange-300/80" data-testid="snapshot-age"
								>snapshot {age} old</span
							>{/if}
					</p>
				</div>
			</div>
		</section>
	{/if}

	{#if (snapshot.fuel ?? []).length}
		<LoomFuelStrip fuel={snapshot.fuel ?? []} bind:body {now} />
	{/if}

	<section class="mt-4" aria-labelledby="threads-heading">
		<div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
			<h2 id="threads-heading" class="eyebrow">threads</h2>
			<div class="flex flex-wrap gap-1" role="group" aria-label="filter threads">
				{#each FILTERS as f (f)}
					<button
						type="button"
						class={`border px-2 py-0.5 font-mono text-[10px] tracking-wide transition-colors ${
							filter === f
								? 'border-amber-600/70 bg-amber-950/40 text-amber-100'
								: 'border-stone-800 text-ink-quiet hover:border-stone-600'
						}`}
						aria-pressed={filter === f}
						data-filter={f}
						onclick={() => (filter = f)}
					>
						{f}
						<span class={f === 'yours' && counts.yours > 0 ? 'text-amber-300' : 'text-ink-mute'}
							>{counts[f]}</span
						>
					</button>
				{/each}
			</div>
		</div>
		<div class="grid gap-2 md:grid-cols-2">
			{#each shown as thread (thread.id)}
				<LoomThreadCard {thread} {now} />
			{:else}
				<p class="subpanel p-3 font-mono text-[11px] text-ink-mute md:col-span-2">
					{threads.length ? `no thread is ${filter}` : 'no threads yet: write a letter to inbox'}
				</p>
			{/each}
		</div>
	</section>

	<section
		class="panel ignite mt-5 p-4"
		style="--ignite-delay: 120ms"
		aria-labelledby="log-heading"
	>
		<h2 id="log-heading" class="eyebrow mb-2">log</h2>
		<LoomLog log={snapshot.log ?? []} {seenAt} {now} />
	</section>

	<section
		class="panel ignite mt-5 p-4"
		style="--ignite-delay: 160ms"
		aria-labelledby="write-heading"
	>
		<h2 id="write-heading" class="eyebrow mb-2">write</h2>
		<LoomComposer {threads} {body} />
	</section>
</main>
