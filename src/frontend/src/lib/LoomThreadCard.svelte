<script lang="ts">
	// One thread on the board. Whose turn it is reads before anything else:
	// yours is the one warm, glowing thing on the card; mine is a calm live
	// dot; blocked goes muted and names what it waits on. Everything else
	// (plan bar, strands, origin, receipt, age) is there for the second look.
	import { resolve } from '$app/paths';
	import LoomStrandBadge from './LoomStrandBadge.svelte';
	import {
		ago,
		latestReceipt,
		originLabel,
		planFraction,
		type LoomThread
	} from './loomSnapshot.ts';

	let { thread, now }: { thread: LoomThread; now: number } = $props();

	let turn = $derived(thread.turn ?? 'none');
	let fraction = $derived(planFraction(thread));
	let receipt = $derived(latestReceipt(thread));
	let origin = $derived(originLabel(thread.opened_by));
	let settled = $derived(thread.status === 'settled');

	const frame: Record<string, string> = {
		yours: 'border-l-amber-400 bg-amber-950/25 shadow-[inset_3px_0_12px_-6px_rgba(251,191,36,0.6)]',
		mine: 'border-l-sky-400/50',
		blocked: 'border-l-stone-700 opacity-75',
		none: 'border-l-stone-800'
	};
	const bar: Record<string, string> = {
		yours: 'bg-amber-400',
		mine: 'bg-sky-300/70',
		blocked: 'bg-stone-600',
		none: 'bg-stone-500'
	};
</script>

<article
	class={`subpanel relative min-w-0 border-l-2 ${frame[turn] ?? frame.none}`}
	data-testid="thread-card"
	data-thread={thread.id}
	data-turn={turn}
>
	<a
		href={resolve('/dashboard/thread/[id]', { id: thread.id })}
		class="block p-3 pb-2 focus-visible:outline focus-visible:outline-amber-500/70"
	>
		<div class="flex items-start gap-2.5">
			<span class="mt-1.5 flex size-3 shrink-0 items-center justify-center" aria-hidden="true">
				{#if turn === 'yours'}
					<span
						class="size-2.5 rounded-full bg-amber-400 shadow-[0_0_10px_2px_rgba(251,191,36,0.55)]"
					></span>
				{:else if turn === 'mine'}
					<span class="size-2 rounded-full border border-sky-300/80 bg-sky-300/25"></span>
				{:else if turn === 'blocked'}
					<span class="h-px w-2.5 bg-stone-500"></span>
				{:else}
					<span class="size-1.5 rounded-full bg-stone-700"></span>
				{/if}
			</span>
			<div class="min-w-0 flex-1">
				<h3
					class={`line-clamp-2 text-[15px] leading-snug ${
						turn === 'yours'
							? 'font-semibold text-amber-50'
							: settled
								? 'text-stone-400'
								: 'text-stone-200'
					}`}
				>
					{thread.title || thread.id}
				</h3>
				<p class="mt-0.5 flex flex-wrap items-baseline gap-x-2 font-mono text-[10px] tracking-wide">
					{#if turn === 'yours'}
						<span class="text-amber-300 uppercase">yours</span>
					{:else if turn === 'mine'}
						<span class="text-sky-300/80 uppercase">mine</span>
					{:else if turn === 'blocked'}
						<span class="text-ink-mute uppercase">blocked</span>
					{/if}
					{#if turn === 'blocked' && thread.blocked_on}
						<span class="text-ink-quiet">waits on {thread.blocked_on}</span>
					{/if}
					{#if settled}<span class="text-ink-mute uppercase">settled</span>{/if}
					<span class="text-ink-mute">{thread.id}</span>
				</p>
			</div>
			{#if ago(thread.last_at, now)}
				<span class="shrink-0 font-mono text-[10px] text-ink-quiet">{ago(thread.last_at, now)}</span
				>
			{/if}
		</div>

		{#if fraction !== null}
			<div class="mt-2.5 ml-5.5 flex items-center gap-2 font-mono text-[10px] text-ink-quiet">
				<span class="relative block h-0.5 flex-1 bg-stone-800" aria-hidden="true">
					<span
						class={`absolute inset-y-0 left-0 ${bar[turn] ?? bar.none}`}
						style={`width: ${fraction * 100}%`}
					></span>
				</span>
				<span>plan {thread.plan?.done}/{thread.plan?.total}</span>
			</div>
			{#if thread.plan?.next && !settled}
				<p class="mt-1 ml-5.5 truncate font-mono text-[11px] text-ink-quiet">
					<span class="text-ink-mute">next</span>
					{thread.plan.next}
				</p>
			{/if}
		{/if}
	</a>

	<div class="flex flex-wrap items-center gap-1.5 px-3 pb-2.5 pl-8.5">
		{#each thread.strands ?? [] as strand (strand.id)}
			<LoomStrandBadge {strand} />
		{/each}
		{#if origin}
			<span class="font-mono text-[10px] text-ink-mute">{origin}</span>
		{/if}
		{#if receipt?.ref}
			<!-- eslint-disable svelte/no-navigation-without-resolve -- an external receipt -->
			<a
				href={receipt.ref}
				target="_blank"
				rel="noopener noreferrer"
				class="ml-auto max-w-[60%] truncate font-mono text-[10px] text-sky-300/90 hover:text-sky-200"
				data-testid="receipt-link"
			>
				↗ {receipt.line || receipt.kind || 'receipt'}
			</a>
			<!-- eslint-enable svelte/no-navigation-without-resolve -->
		{/if}
	</div>
</article>
