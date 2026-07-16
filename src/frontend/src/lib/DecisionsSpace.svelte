<script lang="ts">
	import { fade } from 'svelte/transition';
	import { SvelteSet } from 'svelte/reactivity';

	import { STATUS_COOLING } from './statusPalette';
	import {
		daysSince,
		parseDecisions,
		parsePlan,
		type PlansResponse,
		type RankedMove
	} from './plans';

	interface Props {
		data: PlansResponse;
		now: number;
	}

	let { data, now }: Props = $props();

	// Staleness bar: the design doc's corroborating evidence was four
	// director-tick rows sitting stuck for 2+ days, invisible until a
	// screenshot audit. Day granularity, from the plan's own `Updated:`
	// line — the resident's claim about itself, which is exactly the thing
	// worth badging when it goes quiet.
	const STALE_DAYS = 2;

	let openSections = new SvelteSet<string>();
	let openDecisions = new SvelteSet<string>();
	let showAllDecisions = $state(false);

	function toggle(set: SvelteSet<string>, key: string) {
		if (set.has(key)) set.delete(key);
		else set.add(key);
	}

	let repoPlans = $derived(
		data.plans.map((p) => ({ label: p.repo_label, parsed: parsePlan(p.plan_md) }))
	);
	let crossRepo = $derived(data.cross_repo_plan_md ? parsePlan(data.cross_repo_plan_md) : null);
	let decisions = $derived(parseDecisions(data.decisions_md).reverse());
	let visibleDecisions = $derived(showAllDecisions ? decisions : decisions.slice(0, 6));

	function moveKey(planLabel: string, m: RankedMove): string {
		return `${planLabel}::move::${m.rank}`;
	}
</script>

{#snippet plan(label: string, parsed: ReturnType<typeof parsePlan>)}
	{@const stale = daysSince(parsed.updatedDate, now)}
	<div class="subpanel p-2.5 text-xs">
		<div class="flex items-start justify-between gap-3">
			<p class="truncate font-medium text-amber-100">{label}</p>
			<span class="shrink-0 font-mono text-stone-500">
				{#if parsed.updatedDate}
					updated {parsed.updatedDate}
					{#if stale !== null && stale >= STALE_DAYS}
						<span
							class="ml-1 rounded border px-1"
							style={`border-color: ${STATUS_COOLING}55; background-color: ${STATUS_COOLING}16; color: ${STATUS_COOLING}`}
							title="the plan's own Updated: line is {stale} days old">stale {stale}d</span
						>
					{/if}
				{:else}
					<span class="text-stone-600">no Updated: line</span>
				{/if}
			</span>
		</div>

		{#each parsed.sections as section (label + section.title)}
			{#if section.moves.length > 0}
				<!-- The ranked-move list: the actual scheduling mechanism,
				     rendered as first-class items rather than buried prose. -->
				<p
					class="mt-2 border-t border-stone-800/70 pt-2 text-[10px] tracking-wide text-stone-500 uppercase"
				>
					{section.title}
				</p>
				<ol class="mt-1 space-y-1">
					{#each section.moves as move (moveKey(label, move))}
						{@const key = moveKey(label, move)}
						{@const isOpen = openSections.has(key)}
						<li>
							<button
								type="button"
								class="flex w-full items-baseline gap-2 text-left"
								onclick={() => toggle(openSections, key)}
								aria-expanded={isOpen}
							>
								<span class="shrink-0 font-mono text-amber-400">{move.rank}.</span>
								<span class="min-w-0 flex-1 truncate font-medium text-stone-200">{move.label}</span>
								{#if move.detail}
									<span class="shrink-0 font-mono text-[10px] text-stone-600"
										>{isOpen ? '▲' : '▼'}</span
									>
								{/if}
							</button>
							{#if isOpen && move.detail}
								<p
									class="mt-1 ml-5 whitespace-pre-wrap text-stone-400"
									in:fade={{ duration: 140 }}
									out:fade={{ duration: 100 }}
								>
									{move.detail}
								</p>
							{/if}
						</li>
					{/each}
				</ol>
			{:else if section.title}
				{@const key = `${label}::${section.title}`}
				{@const isOpen = openSections.has(key)}
				<button
					type="button"
					class="mt-1.5 flex w-full items-center justify-between gap-2 text-left font-mono text-stone-400 hover:text-stone-200"
					onclick={() => toggle(openSections, key)}
					aria-expanded={isOpen}
				>
					<span class="truncate">§ {section.title}</span>
					<span class="shrink-0 text-[10px] text-stone-600">{isOpen ? '▲' : '▼'}</span>
				</button>
				{#if isOpen}
					<p
						class="mt-1 whitespace-pre-wrap border-l border-stone-800 pl-2 text-stone-400"
						in:fade={{ duration: 140 }}
						out:fade={{ duration: 100 }}
					>
						{section.body}
					</p>
				{/if}
			{/if}
		{/each}
	</div>
{/snippet}

<div class="panel space-y-2 p-4">
	<div class="mb-1 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">decisions space</span
		>
		{#if data.reported_at}
			<span class="font-mono text-[10px] text-stone-600"
				>mirrored {new Date(data.reported_at).toLocaleString()}</span
			>
		{/if}
	</div>

	{#if repoPlans.length === 0 && !crossRepo && decisions.length === 0}
		<p class="text-sm text-stone-500">No plan or decision ledger mirrored yet.</p>
	{/if}

	{#each repoPlans as rp (rp.label)}
		{@render plan(rp.label, rp.parsed)}
	{/each}
	{#if crossRepo}
		{@render plan('cross-repo', crossRepo)}
	{/if}

	{#if decisions.length > 0}
		<div class="subpanel p-2.5 text-xs">
			<p class="text-[10px] tracking-wide text-stone-500 uppercase">
				decision ledger · latest first
			</p>
			<ul class="mt-1 space-y-1">
				{#each visibleDecisions as d (d.title + (d.date ?? ''))}
					{@const key = `decision::${d.title}`}
					{@const isOpen = openDecisions.has(key)}
					<li>
						<button
							type="button"
							class="flex w-full items-baseline gap-2 text-left"
							onclick={() => toggle(openDecisions, key)}
							aria-expanded={isOpen}
						>
							<span class="shrink-0 font-mono text-stone-600">{d.date ?? '—'}</span>
							<span class="min-w-0 flex-1 truncate text-stone-200">{d.title}</span>
							<span class="shrink-0 font-mono text-[10px] text-stone-600">{isOpen ? '▲' : '▼'}</span
							>
						</button>
						{#if isOpen}
							<p
								class="mt-1 whitespace-pre-wrap border-l border-stone-800 pl-2 text-stone-400"
								in:fade={{ duration: 140 }}
								out:fade={{ duration: 100 }}
							>
								{d.body}
							</p>
						{/if}
					</li>
				{/each}
			</ul>
			{#if decisions.length > 6}
				<button
					type="button"
					class="mt-2 font-mono text-[10px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
					onclick={() => (showAllDecisions = !showAllDecisions)}
				>
					{showAllDecisions ? '▲ latest 6 only' : `▼ all ${decisions.length} decisions`}
				</button>
			{/if}
		</div>
	{/if}
</div>
