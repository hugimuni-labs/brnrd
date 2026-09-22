<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import {
		askHandle,
		askInTopics,
		askLabel,
		attemptGlyph,
		bucketAsks,
		doneWindow,
		keymapIgnores,
		liveAttempt,
		moveFocus,
		sayText,
		splitAlive,
		touchedLabel,
		type AskGoal,
		type AskRow,
		type AsksResponse
	} from './asks';
	// The roast (evt-…-m7n6): "make it more visually distinct, not just a
	// different header in the same table… I want the active items sort of
	// visually lit." `glowFor`/`STATUS_BURNING` are the exact tokens the
	// machine's own lane (`PickLane.svelte`) uses for its picking-row block —
	// reused here rather than a second amber invented for this panel.
	import { glowFor, STATUS_BURNING } from './statusPalette';
	// The 17:51Z steer on the-panel-third-pass: run ids in `attempts` link
	// to the run page — same builder `LiveRuns.svelte`/`RunLedgerReceipt.svelte`
	// already call, never a second URL scheme invented here.
	import { runNodeHref } from './runNode';

	interface Props {
		data: AsksResponse | null;
		error?: string | null;
		now: number;
		/** Run ids live right now — an ask whose `attempts` hold one wears the drone. */
		liveRunIds?: ReadonlySet<string>;
		/** The heddle filter's lit set (`null` = all lit) and slug → canonical id. */
		selected?: ReadonlySet<string> | null;
		resolveTopic?: (slug: string) => string | null;
		/** Topic id/alias → rendered glyph (the heddle rail's own map, e.g.
		 *  `(id) => topicFace(warpGraphData.topicByAlias.get(id)).glyph`) —
		 *  beside an `attempts` run link. `null`/unset ⇒ no glyph, never a
		 *  placeholder. */
		glyphForTopic?: (topic: string) => string | null;
		/** The account's one connected repo, when it has exactly one — an
		 *  `attempts` run id has no repo of its own in this payload, so a
		 *  run link only renders when the repo is unambiguous; `null` ⇒ the
		 *  run id renders as plain text, same as before this landed. */
		runRepoLabel?: string | null;
	}

	let {
		data,
		error = null,
		now,
		liveRunIds = new Set<string>(),
		selected = null,
		resolveTopic = () => null,
		glyphForTopic = () => null,
		runRepoLabel = null
	}: Props = $props();

	const lit = (row: AskRow) => askInTopics(row, selected, resolveTopic);
	// design-the-ask.md §Done, reopened, linked, "the console = the warp
	// panel, per ask"; reordered the-panel-third-pass (his: "the done
	// block should be … on top"): done & accepted · in hand · yours to
	// judge · unaddressed. `buckets` here is still the three open ones —
	// done rides apart, already: `data.done`. Rows arrive LRU-sorted from
	// the server; filtering into buckets keeps that order inside each one,
	// so a live-strand ask does not have to also be the most recently
	// touched to sit on top of its own bucket.
	let buckets = $derived(bucketAsks((data?.asks ?? []).filter(lit)));
	let doneRows = $derived((data?.done ?? []).filter(lit));
	let goals = $derived((data?.goals ?? []) as AskGoal[]);
	// The panel-third-pass steer (his: "the done block should be
	// collapsible but on top, and showing a few last items"): done moves
	// to the top, its newest three rows already visible, `doneOpen` reveals
	// the rest. `unaddressedOpen` is the mirror of the *old* done toggle —
	// the quiet block, collapsed to a bare count until asked.
	let doneWin = $derived(doneWindow(doneRows));
	let doneOpen = $state(false);
	let doneVisible = $derived(doneOpen ? doneRows : doneWin.visible);
	let unaddressedOpen = $state(false);
	// His 18:27Z follow-up: inside unaddressed, the stale rows (past the
	// horizon) get their own nested collapse — a bare count first
	// (`staleOpen`), the newest three once opened (`doneWindow` reused —
	// same "window + rest toggle" shape as the done bucket above, `splitAlive`
	// already orders stale rows the same newest-first way `done` arrives in),
	// `staleRestOpen` for what's past the window. Non-stale unaddressed rows
	// render as they always have, unaffected.
	let unaddressedSplit = $derived(splitAlive(buckets.unaddressed));
	let staleWin = $derived(doneWindow(unaddressedSplit.stale));
	let staleOpen = $state(false);
	let staleRestOpen = $state(false);
	let staleVisible = $derived(
		!staleOpen ? [] : staleRestOpen ? unaddressedSplit.stale : staleWin.visible
	);
	let expanded = new SvelteSet<string>();
	let focus = $state<number | null>(null);
	let root: HTMLElement | undefined = $state();

	// The order `j`/`k` walk: done's visible window, in hand, to judge, then
	// unaddressed's non-stale rows + its stale sub-window, both gated on the
	// outer unaddressed toggle — the same order the buckets render in.
	let visible = $derived([
		...doneVisible,
		...buckets.inHand,
		...buckets.toJudge,
		...(unaddressedOpen ? [...unaddressedSplit.alive, ...staleVisible] : [])
	]);

	// The pulse's two box-shadow stops, taken directly off `glowFor` (the
	// same call `PickLane.svelte` makes for a burning row) rather than a
	// hand-tuned glow — `glowFor` returns a full `box-shadow: …;` CSS
	// declaration, so only the value survives the strip; the keyframes below
	// reference the pair through CSS custom properties (a component style
	// block is static markup, it cannot close over these).
	const shadowValue = (declaration: string) =>
		declaration.replace(/^box-shadow:\s*/, '').replace(/;\s*$/, '');
	const pulseLow = shadowValue(glowFor('calm', STATUS_BURNING, 'bar'));
	const pulseHigh = shadowValue(glowFor('attention', STATUS_BURNING, 'bar'));

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
     panel, per ask"; reordered the-panel-third-pass): four buckets, one
     column, done-first — **done & accepted** (its newest three open, a
     toggle for the rest) · **in hand** (a strand or the seat is working it
     now) · **yours to judge** (delivered, waiting on accept/reroute) ·
     **unaddressed** (not yet even shaped, collapsed to a count; its own
     stale rows nest one collapse deeper — the 18:27Z follow-up). A stale
     row inside in-hand/to-judge stays dimmed in place, not pulled out —
     only unaddressed's stale rows get the nested block. A row opens in
     place: its says (`<relative time> · <excerpt> · ↗`, the 17:51Z
     steer), its attempts (a run link + topic glyph, same steer), its
     receipt; a `sign` (when a row carries one) speaks in the id cell and
     the accept/reroute chips in place of the bare id. `j`/`k` walk the
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
			{@const boxed = kind === 'inHand'}
			<ul class={boxed ? 'space-y-1' : 'space-y-px'}>
				{#each list as row (row.id)}
					{@const open = expanded.has(row.id)}
					{@const drone = liveAttempt(row, liveRunIds)}
					<li class:opacity-60={row.stale}>
						<button
							type="button"
							class="ask-row block w-full px-2 py-1.5 text-left {boxed
								? `border ${drone ? 'border-amber-400/80' : 'border-amber-700/40'} bg-stone-950/70 hover:bg-stone-900/70 focus-visible:bg-stone-900/70`
								: `border-l-2 ${drone ? 'border-amber-500/70' : 'border-transparent'} hover:bg-stone-800/40 focus-visible:bg-stone-800/50`}"
							class:ask-live-pulse={boxed && Boolean(drone)}
							style={boxed && drone
								? `--ask-pulse-low: ${pulseLow}; --ask-pulse-high: ${pulseHigh};`
								: undefined}
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
								<span
									class="shrink-0 truncate font-mono text-[10px] text-ink-mute {row.sign
										? 'max-w-[10rem]'
										: 'w-12'}"
									title={askLabel(row)}>{askLabel(row)}</span
								>
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
								class="flex flex-wrap items-center gap-x-2 gap-y-1 pb-1.5 pl-16 font-mono text-[10px] text-stone-400"
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
								<!-- His roast: the two words as "a small chip pair, not body
								     text". Still neither a status color (statusPalette.ts: no
								     green family, red reserved for a broken contract) — just
								     the sky link tone and the base ink, boxed rather than bare.
								     `askHandle` speaks the sign back when the row carries one
								     (the-panel-third-pass: "the chip words use the sign when
								     present") — same fallback to the bare id as the id cell. -->
								<span
									class="cursor-text select-all border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 tracking-wide text-sky-300 uppercase"
									>accept {askHandle(row)}</span
								>
								<span
									class="cursor-text select-all border border-stone-700/60 bg-stone-900/40 px-1.5 py-0.5 tracking-wide text-stone-300 uppercase"
									>reroute {askHandle(row)}: &lt;why&gt;</span
								>
							</div>
						{/if}
						{#if open}
							<div class="space-y-1.5 pb-2 pl-16 pr-2 font-mono text-[11px] text-ink-quiet">
								{#if row.after}<p>after <span class="text-stone-300">{row.after}</span></p>{/if}
								<div>
									<p class="text-[10px] tracking-wide text-ink-mute uppercase">says</p>
									<!-- 17:51Z steer (the-panel-third-pass): "the evt-say lines say
									     nothing" — `<relative time> · <excerpt> · ↗`, never the raw
									     event id as the lead (`sayText` falls back to it only when
									     no excerpt resolved); ↗ only when the server sent a `url`
									     (no route exists yet, so never today — see asks.ts). -->
									{#each row.says as say (say.event)}
										<p class="truncate">
											<span class="text-ink-mute">{touchedLabel(say.at, now) || '—'}</span>
											<span class="text-stone-300"> · {sayText(say)}</span>
											{#if say.url}
												<!-- eslint-disable svelte/no-navigation-without-resolve -->
												<a
													href={say.url}
													class="text-sky-200 hover:text-sky-100"
													target="_blank"
													rel="noopener noreferrer"
													aria-label="open this message"
												>
													· ↗</a
												>
												<!-- eslint-enable svelte/no-navigation-without-resolve -->
											{/if}
										</p>
									{:else}
										<p class="text-ink-mute">none recorded yet</p>
									{/each}
								</div>
								<div>
									<p class="text-[10px] tracking-wide text-ink-mute uppercase">attempts</p>
									<!-- 17:51Z steer: a run id links to its run page (`runNodeHref`,
									     same builder LiveRuns/RunLedgerReceipt use) with the ask's
									     own topic glyph beside — the run itself carries no topic in
									     this payload, `attemptGlyph` names that stand-in. No known
									     repo (an account with 0 or >1 connected, `runRepoLabel` null)
									     ⇒ plain text, same as before this landed. -->
									{#each row.attempts as run (run)}
										{@const glyph = attemptGlyph(row, glyphForTopic)}
										<p class="truncate">
											{#if runRepoLabel}<a
													class="text-stone-300 underline decoration-stone-700 hover:text-amber-100"
													href={runNodeHref(runRepoLabel, run)}>{run}</a
												>{:else}<span class="text-stone-300">{run}</span>{/if}
											{#if glyph}<span class="text-ink-mute"> {glyph}</span>{/if}
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

		<!-- The panel, third pass (his: "the done block should be
		     collapsible but on top, and showing a few last items" —
		     "the quiet items blocks should be collapsed"): **done & accepted**
		     leads now, its newest three rows already open (`doneWindow`,
		     asks.ts) so a glance at the top answers "what did I finish" —
		     `doneWin.restCount` rides a `▸ N done` toggle for the rest, never
		     the whole bucket's own heading (that stays a static count: the
		     visible three are not what the toggle hides). **in hand** and
		     **yours to judge** are unchanged, still framed to read as
		     distinct blocks (the roast: "not just a different header in the
		     same table") — live-amber for in hand (`PickLane.svelte`'s
		     picking-row tokens), `.subpanel`'s quieter hairline for to-judge.
		     **unaddressed** sinks to the bottom, now the collapsed-to-a-count
		     block in its place — nothing has happened on these yet, so a
		     bare count is the whole story until asked. A stale row inside any
		     bucket is still dimmed in place (`opacity-60` on its `<li>`,
		     above), never moved to a fifth place. A bucket with nothing in it
		     renders its heading and stops — no empty frame, no dead toggle. -->
		<div class="space-y-4">
			<div class="border-b border-stone-800/60 pb-2 opacity-80" data-bucket="done">
				<p
					class="mb-1 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
					data-bucket-heading="done"
				>
					done &amp; accepted · {doneRows.length}
				</p>
				{#if doneRows.length}
					{@render rows(doneVisible)}
					{#if doneWin.restCount > 0}
						<button
							type="button"
							class="mt-1 flex items-baseline gap-1.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase hover:text-stone-300"
							aria-expanded={doneOpen}
							onclick={() => (doneOpen = !doneOpen)}
							data-bucket-toggle="done">{doneOpen ? '▾' : '▸'} {doneWin.restCount} done</button
						>
					{/if}
				{/if}
			</div>
			<div class="border border-amber-700/50 bg-stone-950/60 p-2" data-bucket="in-hand">
				<p
					class="mb-1 font-mono text-[10px] tracking-wide text-amber-300 uppercase"
					data-bucket-heading="in-hand"
				>
					in hand · {buckets.inHand.length}
				</p>
				{#if buckets.inHand.length}{@render rows(buckets.inHand, 'inHand')}{/if}
			</div>
			<div class="subpanel p-2" data-bucket="to-judge">
				<p
					class="mb-1 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
					data-bucket-heading="to-judge"
				>
					yours to judge · {buckets.toJudge.length}
				</p>
				{#if buckets.toJudge.length}{@render rows(buckets.toJudge, 'toJudge')}{/if}
			</div>
			<div data-bucket="unaddressed">
				{#if buckets.unaddressed.length}
					<button
						type="button"
						class="mb-1 flex w-full items-baseline gap-1.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase hover:text-stone-300"
						aria-expanded={unaddressedOpen}
						onclick={() => (unaddressedOpen = !unaddressedOpen)}
						data-bucket-heading="unaddressed"
						>{unaddressedOpen ? '▾' : '▸'} unaddressed · {buckets.unaddressed.length}</button
					>
					{#if unaddressedOpen}
						{@render rows(unaddressedSplit.alive)}
						<!-- His 18:27Z follow-up: stale unaddressed rows get their own
						     nested block — a bare count, then (opened) the newest three,
						     then (opened further) the rest. Non-stale rows above are
						     unaffected. -->
						{#if unaddressedSplit.stale.length}
							<button
								type="button"
								class="mt-1 flex items-baseline gap-1.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase hover:text-stone-300"
								aria-expanded={staleOpen}
								onclick={() => (staleOpen = !staleOpen)}
								data-bucket-heading="unaddressed-stale"
								>{staleOpen ? '▾' : '▸'} stale · {unaddressedSplit.stale.length}</button
							>
							{#if staleOpen}
								{@render rows(staleVisible)}
								{#if staleWin.restCount > 0}
									<button
										type="button"
										class="mt-1 flex items-baseline gap-1.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase hover:text-stone-300"
										aria-expanded={staleRestOpen}
										onclick={() => (staleRestOpen = !staleRestOpen)}
										data-bucket-toggle="unaddressed-stale"
										>{staleRestOpen ? '▾' : '▸'} {staleWin.restCount} stale</button
									>
								{/if}
							{/if}
						{/if}
					{/if}
				{:else}
					<p
						class="mb-1 font-mono text-[10px] tracking-wide text-ink-mute uppercase"
						data-bucket-heading="unaddressed"
					>
						unaddressed · 0
					</p>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	/* The live row's pulse — the roast's "visually lit", moving. `--ask-pulse-low`
	   / `--ask-pulse-high` are set inline per row from `glowFor` (statusPalette.ts,
	   the same call `PickLane.svelte` makes for a burning pick), so the two stops
	   this animates between are the machine's own tokens, not a value invented
	   here. A `seat` in-hand row (no live attempt) never gets this class — it
	   wears the static amber border only, per the roast: "seat rows: lit, no
	   pulse." Reduced motion holds the high stop rather than dropping to the low
	   one silently zeroing the glow: honest with the animation off, same as
	   `.panel`'s own idle breathe just above it in `layout.css`. */
	.ask-live-pulse {
		animation: ask-pulse 2.4s ease-in-out infinite;
	}

	@keyframes ask-pulse {
		0%,
		100% {
			box-shadow: var(--ask-pulse-low);
		}
		50% {
			box-shadow: var(--ask-pulse-high);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.ask-live-pulse {
			animation: none;
			box-shadow: var(--ask-pulse-high);
		}
	}
</style>
