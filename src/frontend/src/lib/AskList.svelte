<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import {
		askInTopics,
		bucketAsks,
		keymapIgnores,
		liveAttempt,
		moveFocus,
		touchedLabel,
		type AskGoal,
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
	// design-the-ask.md §Done, reopened, linked, "the console = the warp
	// panel, per ask" — his bucket order: in hand · yours to judge ·
	// unaddressed · done & accepted (apart, already: `data.done`). Rows
	// arrive LRU-sorted from the server; filtering into buckets keeps that
	// order inside each one, so a live-strand ask does not have to also be
	// the most recently touched to sit on top of its own bucket.
	let buckets = $derived(bucketAsks((data?.asks ?? []).filter(lit)));
	let doneRows = $derived((data?.done ?? []).filter(lit));
	let goals = $derived((data?.goals ?? []) as AskGoal[]);
	let doneOpen = $state(false);
	let expanded = new SvelteSet<string>();
	let focus = $state<number | null>(null);
	let root: HTMLElement | undefined = $state();

	// The order `j`/`k` walk: in hand, to judge, unaddressed, then done when
	// its toggle is open — the same order the buckets render in.
	let visible = $derived([
		...buckets.inHand,
		...buckets.toJudge,
		...buckets.unaddressed,
		...(doneOpen ? doneRows : [])
	]);

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

<!-- The console's list of asks, replacing the warp's item graph in place
     (design-the-ask.md §Done, reopened, linked, "the console = the warp
     panel, per ask" — his order): four buckets, one column — **in hand**
     (a strand or the seat is working it now) · **yours to judge**
     (delivered, waiting on accept/reroute) · **unaddressed** (not yet even
     shaped) · **done & accepted** (collapsed to a count). Rows arrive
     LRU-sorted from the server; a stale row stays in its bucket, dimmed,
     rather than sinking below a rule — the bucket already says what stage
     it is in, so a second sort axis inside it would just be noise. A row
     opens in place: its says, its attempts, its receipt. `j`/`k` walk the
     rows in bucket order, Enter opens one (a row is a real button, so
     Enter/Space are the browser's own). -->
<div class="panel mt-2 p-3" aria-label="your asks" bind:this={root}>
	<div class="mb-2 flex items-baseline justify-between gap-3">
		<span class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">your asks</span>
	</div>
	{#if error}
		<p class="text-sm text-red-400">{error}</p>
	{:else if data === null}
		<p class="text-sm text-ink-quiet">listening for asks…</p>
	{:else if buckets.inHand.length + buckets.toJudge.length + buckets.unaddressed.length + doneRows.length === 0}
		<p class="text-sm text-ink-quiet">
			no asks yet — the first one you make lands here, in your words.
		</p>
	{:else}
		{#if goals.length}
			<!-- His order (design-the-ask.md): goals stay goals, above the buckets
			     that track the asks working toward them — two lines, clamped, so a
			     long goal list never pushes "in hand" below the fold. -->
			<p class="mb-2 line-clamp-2 font-mono text-[10px] text-ink-quiet" data-goals>
				<span class="tracking-wide text-ink-mute uppercase">goals</span>
				· {goals.map((g) => g.title).join(' · ')}
			</p>
		{/if}
		{#snippet rows(list: AskRow[], kind: 'inHand' | 'toJudge' | 'plain' = 'plain')}
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
								{#if kind === 'inHand'}
									<!-- "in hand" needs no design beyond this (design-the-ask.md):
									     who's holding it right now. -->
									<span class="text-amber-300" data-in-hand>{drone ? `▷ ${drone}` : 'seat'}</span>
								{:else}
									{#if row.return}<span>{row.return}</span>{/if}
									{#if row.stage}<span class="text-ink-mute">· {row.stage}</span>{/if}
								{/if}
								<span class="text-ink-mute"
									>· {row.says.length} say{row.says.length === 1 ? '' : 's'}</span
								>
							</span>
						</button>
						{#if kind === 'toJudge'}
							<!-- accept/reroute are chat words first (design-the-ask.md): a
							     copyable hint, never a button — parsed by the seat, kept by
							     the ledger. Outside the `<button>` so its receipt link stays
							     valid HTML (no interactive content nested in a button). -->
							<div
								class="flex flex-wrap items-baseline gap-x-3 pb-1.5 pl-16 font-mono text-[10px] text-stone-400"
								data-to-judge-hints
							>
								{#if row.receipt}
									<span
										>receipt:
										{#if isLink(row.receipt)}
											<!-- eslint-disable svelte/no-navigation-without-resolve -->
											<a
												href={row.receipt}
												class="text-sky-200 hover:text-sky-100"
												target="_blank"
												rel="noopener noreferrer">{row.receipt.replace(/^https?:\/\//, '')}</a
											>
											<!-- eslint-enable svelte/no-navigation-without-resolve -->
										{:else}{row.receipt}{/if}</span
									>
								{/if}
								<!-- Neither word is a status color (statusPalette.ts: no green
								     family, red reserved for a broken contract) — just the
								     sky link tone and the base ink, same as the rest of the row. -->
								<span class="cursor-text select-all text-sky-200/80">accept {row.id}</span>
								<span class="cursor-text select-all text-stone-300"
									>reroute {row.id}: &lt;why&gt;</span
								>
							</div>
						{/if}
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

		<!-- The four buckets, his order, each headed and counted
		     (design-the-ask.md) — a stale row inside one is dimmed
		     (`opacity-60` on its `<li>`, above), never moved to a fifth
		     place: the bucket already says what stage the ask is in. -->
		<div class="space-y-3">
			<div>
				<p
					class="mb-1 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
					data-bucket-heading="in-hand"
				>
					in hand · {buckets.inHand.length}
				</p>
				{@render rows(buckets.inHand, 'inHand')}
			</div>
			<div>
				<p
					class="mb-1 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
					data-bucket-heading="to-judge"
				>
					yours to judge · {buckets.toJudge.length}
				</p>
				{@render rows(buckets.toJudge, 'toJudge')}
			</div>
			<div>
				<p
					class="mb-1 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
					data-bucket-heading="unaddressed"
				>
					unaddressed · {buckets.unaddressed.length}
				</p>
				{@render rows(buckets.unaddressed)}
			</div>
			<div>
				<button
					type="button"
					class="mb-1 flex w-full items-baseline gap-1.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase hover:text-stone-300"
					aria-expanded={doneOpen}
					onclick={() => (doneOpen = !doneOpen)}
					data-bucket-heading="done"
					>{doneOpen ? '▾' : '▸'} done &amp; accepted · {doneRows.length}</button
				>
				{#if doneOpen}{@render rows(doneRows)}{/if}
			</div>
		</div>
	{/if}
</div>
