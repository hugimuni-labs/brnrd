<script lang="ts">
	// One thread: its Plan, its Done, its letters, its strands, and a
	// composer addressed to it. Reads the same snapshot the board does.
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import LoomComposer from './LoomComposer.svelte';
	import LoomFuelStrip from './LoomFuelStrip.svelte';
	import LoomLog from './LoomLog.svelte';
	import LoomStrandBadge from './LoomStrandBadge.svelte';
	import { startVisiblePoll } from './visiblePoll.ts';
	import {
		BODY_KEY,
		SEEN_KEY,
		ago,
		defaultBody,
		fetchLoom,
		keptBody,
		originLabel,
		planFraction,
		store,
		stored,
		type LoomRead
	} from './loomSnapshot.ts';

	let { id }: { id: string } = $props();

	let read = $state<LoomRead | 'error' | null>(null);
	let seenAt = $state<string | null>(null);
	let body = $state<string | null>(null);
	let now = $state(Date.now());

	let snapshot = $derived(read && typeof read === 'object' ? read : null);
	let thread = $derived(snapshot?.threads?.find((t) => t.id === id) ?? null);
	let fraction = $derived(thread ? planFraction(thread) : null);
	let turn = $derived(thread?.turn ?? 'none');

	onMount(() => {
		seenAt = stored(SEEN_KEY);
		const clock = setInterval(() => (now = Date.now()), 30_000);
		const stop = startVisiblePoll(
			async (signal) => {
				const next = await fetchLoom(fetch, signal).catch(() => 'error' as const);
				if (signal.aborted) return;
				// Keep the last good snapshot through a failed refresh.
				if (next === 'error' && snapshot) return;
				read = next;
				if (typeof next === 'object') body ??= keptBody(next);
			},
			document,
			30_000
		);
		return () => {
			clearInterval(clock);
			stop();
		};
	});

	$effect(() => {
		if (snapshot && body) store(BODY_KEY, body);
	});
</script>

<main class="mx-auto max-w-3xl px-5 pb-16 sm:px-6" data-testid="loom-thread">
	<header class="flex items-center justify-between pt-4 pb-3">
		<a href={resolve('/dashboard')} class="font-mono text-[11px] text-ink-quiet hover:text-amber-200"
			>← threads</a
		>
		<p class="eyebrow eyebrow--asis">{id}</p>
	</header>

	{#if read === null}
		<p class="eyebrow py-10 text-center">reaching brnrd&hellip;</p>
	{:else if read === 'anon'}
		<p class="subpanel p-4 font-mono text-sm text-stone-300">
			signed out. <a href={resolve('/login')} class="text-amber-200 underline">sign in</a> to see this
			thread.
		</p>
	{:else if read === 'none' || read === 'error'}
		<p class="subpanel p-4 font-mono text-sm text-stone-300">
			{read === 'none' ? 'the loom has not published yet.' : 'brnrd did not answer.'}
			<a href={resolve('/dashboard')} class="text-amber-200 underline">home</a>
		</p>
	{:else if !thread}
		<p class="subpanel p-4 font-mono text-sm text-stone-300">
			no thread named {id} on the loom.
			<a href={resolve('/dashboard')} class="text-amber-200 underline">threads</a>
		</p>
	{:else}
		<section class="panel ignite p-4" style="--ignite-delay: 40ms" data-turn={turn}>
			<h1
				class={`text-lg leading-snug ${turn === 'yours' ? 'font-semibold text-amber-50' : 'text-stone-100'}`}
			>
				{thread.title || thread.id}
			</h1>
			<p class="mt-1.5 flex flex-wrap gap-x-3 font-mono text-[10px] tracking-wide text-ink-quiet">
				{#if turn === 'yours'}
					<span class="flex items-center gap-1.5 text-amber-300 uppercase"
						><span
							class="size-2 rounded-full bg-amber-400 shadow-[0_0_8px_2px_rgba(251,191,36,0.5)]"
						></span>yours</span
					>
				{:else if turn === 'mine'}
					<span class="text-sky-300/80 uppercase">mine</span>
				{:else if turn === 'blocked'}
					<span class="uppercase"
						>blocked{thread.blocked_on ? ` · waits on ${thread.blocked_on}` : ''}</span
					>
				{/if}
				{#if thread.status}<span>{thread.status}</span>{/if}
				{#if originLabel(thread.opened_by)}<span>{originLabel(thread.opened_by)}</span>{/if}
				{#if thread.owed}<span class="text-amber-300/90">owes {thread.owed}</span>{/if}
				{#if ago(thread.last_at, now)}<span>{ago(thread.last_at, now)}</span>{/if}
			</p>
		</section>

		{#if (snapshot?.fuel ?? []).length}
			<div class="mt-3"><LoomFuelStrip fuel={snapshot?.fuel ?? []} bind:body {now} /></div>
		{/if}

		<div class="mt-4 grid gap-4 md:grid-cols-2">
			<section class="subpanel p-3" aria-labelledby="plan-heading">
				<h2 id="plan-heading" class="eyebrow mb-2">Plan</h2>
				{#if fraction !== null}
					<div class="flex items-center gap-2 font-mono text-[11px] text-ink-quiet">
						<span class="relative block h-1 flex-1 bg-stone-800">
							<span
								class={`absolute inset-y-0 left-0 ${turn === 'yours' ? 'bg-amber-400' : 'bg-sky-300/70'}`}
								style={`width: ${fraction * 100}%`}
							></span>
						</span>
						<span>{thread.plan?.done}/{thread.plan?.total}</span>
					</div>
					{#if thread.plan?.next}
						<p class="mt-2 text-sm text-stone-200">
							<span class="font-mono text-[10px] text-ink-mute uppercase">next</span>
							{thread.plan.next}
						</p>
					{/if}
				{:else}
					<p class="font-mono text-[11px] text-ink-mute">no plan published</p>
				{/if}
			</section>

			<section class="subpanel p-3" aria-labelledby="strands-heading">
				<h2 id="strands-heading" class="eyebrow mb-2">strands</h2>
				<ul class="space-y-1.5">
					{#each thread.strands ?? [] as strand (strand.id)}
						<li class="flex flex-wrap items-center gap-2">
							<LoomStrandBadge {strand} />
							<span class="font-mono text-[10px] text-ink-mute">{strand.id}</span>
							{#if ago(strand.since, now)}
								<span class="font-mono text-[10px] text-ink-mute"
									>since {ago(strand.since, now)}</span
								>
							{/if}
						</li>
					{:else}
						<li class="font-mono text-[11px] text-ink-mute">no strand on it</li>
					{/each}
				</ul>
			</section>
		</div>

		<section class="subpanel mt-4 p-3" aria-labelledby="done-heading">
			<h2 id="done-heading" class="eyebrow mb-2">Done</h2>
			<ul class="space-y-1.5" data-testid="done">
				{#each thread.done ?? [] as item, i (i)}
					<li class="flex gap-2 text-sm">
						<span class="w-8 shrink-0 font-mono text-[10px] text-ink-mute uppercase"
							>{item.kind ?? ''}</span
						>
						{#if item.ref}
							<!-- eslint-disable svelte/no-navigation-without-resolve -- an external receipt -->
							<a
								href={item.ref}
								target="_blank"
								rel="noopener noreferrer"
								class="text-sky-200 hover:text-sky-100">↗ {item.line || item.ref}</a
							>
							<!-- eslint-enable svelte/no-navigation-without-resolve -->
						{:else}
							<span class="text-stone-300">{item.line}</span>
						{/if}
					</li>
				{:else}
					<li class="font-mono text-[11px] text-ink-mute">nothing delivered yet</li>
				{/each}
			</ul>
		</section>

		<section class="panel mt-4 p-4" aria-labelledby="letters-heading">
			<h2 id="letters-heading" class="eyebrow mb-2">letters</h2>
			<LoomLog log={snapshot?.log ?? []} {seenAt} {now} thread={thread.id} />
		</section>

		<section class="panel mt-4 p-4" aria-labelledby="write-heading">
			<h2 id="write-heading" class="eyebrow mb-2">write</h2>
			<LoomComposer
				threads={snapshot?.threads ?? []}
				thread={thread.id}
				{body}
				fallback={snapshot ? defaultBody(snapshot) : null}
				fixed
			/>
		</section>
	{/if}
</main>
