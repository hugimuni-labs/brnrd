<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import {
		askInTopics,
		keymapIgnores,
		liveAttempt,
		moveFocus,
		splitAlive,
		touchedLabel,
		type AskRow,
		type AsksResponse
	} from './asks';

	interface Props {
		data: AsksResponse | null;
		error?: string | null;
		now: number;
		/** Run ids live right now — an ask whose `attempts` hold one wears the drone. */
		liveRunIds?: ReadonlySet<string>;
		/** The heddle filter's lit set (`null` = all lit) and slug → canonical id. */
		selected?: ReadonlySet<string> | null;
		resolveTopic?: (slug: string) => string | null;
	}

	let {
		data,
		error = null,
		now,
		liveRunIds = new Set<string>(),
		selected = null,
		resolveTopic = () => null
	}: Props = $props();

	const lit = (row: AskRow) => askInTopics(row, selected, resolveTopic);
	let bands = $derived(splitAlive((data?.asks ?? []).filter(lit)));
	let doneRows = $derived((data?.done ?? []).filter(lit));
	let doneOpen = $state(false);
	let expanded = new SvelteSet<string>();
	let focus = $state<number | null>(null);
	let root: HTMLElement | undefined = $state();

	// The order `j`/`k` walk: alive, stale, then done when its toggle is open.
	let visible = $derived([...bands.alive, ...bands.stale, ...(doneOpen ? doneRows : [])]);

	function toggle(id: string) {
		if (expanded.has(id)) expanded.delete(id);
		else expanded.add(id);
	}

	function onKey(event: KeyboardEvent) {
		if (event.key !== 'j' && event.key !== 'k') return;
		if (keymapIgnores(event.target, event)) return;
		const next = moveFocus(focus, event.key, visible.length);
		if (next === null) return;
		event.preventDefault();
		focus = next;
		root?.querySelector<HTMLElement>(`[data-ask="${visible[next].id}"]`)?.focus();
	}

	onMount(() => {
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});

	const isLink = (value: string) => /^https?:\/\//.test(value);
</script>

<!-- The console's list of asks (design-the-ask.md §Build cut 3): the home *is*
     the list, not a warp page with a sort control. LRU — the row touched last
     is on top; rows past the stale horizon sink under a thin rule; done rows
     sit under a second rule, collapsed to a count. A row opens in place: its
     says, its attempts, its receipt. `j`/`k` walk the rows, Enter opens one
     (a row is a real button, so Enter/Space are the browser's own). -->
<div class="panel mt-2 p-3" aria-label="your asks" bind:this={root}>
	<div class="mb-2 flex items-baseline justify-between gap-3">
		<span class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">your asks</span>
		<span class="font-mono text-[10px] text-ink-mute">
			{#if data}{bands.alive.length} alive{#if bands.stale.length}
					· {bands.stale.length} stale{/if}{/if}
		</span>
	</div>
	{#if error}
		<p class="text-sm text-red-400">{error}</p>
	{:else if data === null}
		<p class="text-sm text-ink-quiet">listening for asks…</p>
	{:else if bands.alive.length + bands.stale.length + doneRows.length === 0}
		<p class="text-sm text-ink-quiet">
			no asks yet — the first one you make lands here, in your words.
		</p>
	{:else}
		{#snippet rows(list: AskRow[])}
			<ul class="space-y-px">
				{#each list as row (row.id)}
					{@const open = expanded.has(row.id)}
					{@const drone = liveAttempt(row, liveRunIds)}
					<li
						class="border-l-2 {drone ? 'border-amber-500/70' : 'border-transparent'}"
						class:opacity-60={row.stale}
					>
						<button
							type="button"
							class="ask-row block w-full px-2 py-1.5 text-left hover:bg-stone-800/40 focus-visible:bg-stone-800/50"
							data-ask={row.id}
							data-ask-stale={row.stale ? '' : undefined}
							data-ask-done={row.done ? '' : undefined}
							aria-expanded={open}
							onclick={() => {
								toggle(row.id);
								focus = visible.findIndex((candidate) => candidate.id === row.id);
							}}
						>
							<span class="flex items-baseline gap-2">
								<span class="w-12 shrink-0 font-mono text-[10px] text-ink-mute">{row.id}</span>
								<span class="min-w-0 flex-1 truncate text-sm text-amber-100" title={row.title}
									>{row.title}</span
								>
								{#if drone}<span
										class="shrink-0 font-mono text-xs text-amber-300"
										data-drone
										title="a live strand wears this ask: {drone}"
										aria-label="a live strand is working this ask">⌁</span
									>{/if}
								<span class="shrink-0 font-mono text-[10px] text-ink-quiet tabular-nums"
									>{touchedLabel(row.touched_at, now) || '—'}</span
								>
							</span>
							<span
								class="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 pl-14 font-mono text-[10px] text-ink-quiet"
							>
								{#if row.type}<span class="uppercase tracking-wide text-sky-200/80">{row.type}</span
									>{/if}
								{#if row.return}<span>{row.return}</span>{/if}
								{#if row.stage}<span class="text-ink-mute">· {row.stage}</span>{/if}
								<span class="text-ink-mute"
									>· {row.says.length} say{row.says.length === 1 ? '' : 's'}</span
								>
							</span>
						</button>
						{#if open}
							<div class="space-y-1.5 pb-2 pl-16 pr-2 font-mono text-[11px] text-ink-quiet">
								{#if row.after}<p>after <span class="text-stone-300">{row.after}</span></p>{/if}
								<div>
									<p class="text-[10px] tracking-wide text-ink-mute uppercase">says</p>
									{#each row.says as say (say.event)}
										<p class="truncate">
											<span class="text-stone-400">{say.event.slice(0, 30)}</span>
											{#if say.at}<span class="text-ink-mute">
													· {touchedLabel(say.at, now)}</span
												>{/if}
											{#if say.excerpt}<span class="text-stone-300"> · “{say.excerpt}”</span>{/if}
										</p>
									{:else}
										<p class="text-ink-mute">none recorded yet</p>
									{/each}
								</div>
								<div>
									<p class="text-[10px] tracking-wide text-ink-mute uppercase">attempts</p>
									{#each row.attempts as run (run)}
										<p class="truncate">
											<span class="text-stone-300">{run}</span>
											{#if liveRunIds.has(run)}<span class="text-amber-300"> · live</span>{/if}
										</p>
									{:else}
										<p class="text-ink-mute">none yet</p>
									{/each}
								</div>
								{#if row.receipt}
									<p>
										<span class="text-[10px] tracking-wide text-ink-mute uppercase">receipt</span>
										{#if isLink(row.receipt)}
											<!-- an external forge URL (`isLink` gates it), never an app route -->
											<!-- eslint-disable svelte/no-navigation-without-resolve -->
											<a
												href={row.receipt}
												class="text-sky-200 hover:text-sky-100"
												target="_blank"
												rel="noopener noreferrer">{row.receipt.replace(/^https?:\/\//, '')}</a
											>
											<!-- eslint-enable svelte/no-navigation-without-resolve -->
										{:else}<span class="text-stone-300">{row.receipt}</span>{/if}
									</p>
								{/if}
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/snippet}

		{@render rows(bands.alive)}
		{#if bands.stale.length}
			<div class="my-2 flex items-center gap-2 font-mono text-[10px] text-ink-mute" data-stale-rule>
				<span class="h-px flex-1 bg-stone-800"></span>
				<span>quiet for {data.stale_after_days}+ days</span>
				<span class="h-px flex-1 bg-stone-800"></span>
			</div>
			{@render rows(bands.stale)}
		{/if}
		{#if doneRows.length}
			<div class="my-2 flex items-center gap-2 font-mono text-[10px] text-ink-mute">
				<span class="h-px flex-1 bg-stone-800"></span>
				<button
					type="button"
					class="hover:text-stone-300"
					aria-expanded={doneOpen}
					onclick={() => (doneOpen = !doneOpen)}
					>{doneOpen ? '▾' : '▸'} {doneRows.length} done</button
				>
				<span class="h-px flex-1 bg-stone-800"></span>
			</div>
			{#if doneOpen}{@render rows(doneRows)}{/if}
		{/if}
	{/if}
</div>
