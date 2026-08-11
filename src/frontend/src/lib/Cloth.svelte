<script lang="ts">
	import { fade } from 'svelte/transition';
	import { SvelteSet } from 'svelte/reactivity';
	import { glitchReveal } from './transitions';
	import { loomPastWindowLabel } from './loomBand';
	import {
		CLOTH_ROOT_CAP,
		clothSelvage,
		groupClothDays,
		inClothWindow,
		selvageParts,
		weaveCloth,
		type ClothLine,
		type ClothTree
	} from './cloth';
	import { LENS_ALL, applyLens, availableLenses, reconcileLens } from './loomLens';
	import type { RunLedgerRow } from './runLedger';
	import RunNodeInline from './RunNodeInline.svelte';
	import Crossing from './Crossing.svelte';
	import { crossingCells } from './crossing';
	import {
		nodeDigest,
		repoRunSlug,
		runIdSlug,
		runNodeFromSurface,
		type NodeIdentity
	} from './runNode';
	import { runFace, type RunFace } from './runFace';
	import type { SurfaceResponse } from './surface';
	import MoodChip from './MoodChip.svelte';

	// The cloth — the past band, v1 (design-work-layers.md). The window's
	// done work as root-run trees, one curated line each, strands
	// folded beneath and expanded on demand; the selvage (the cloth's
	// self-finished edge) runs across the top as one compact spend→produce
	// row. The page owns the rows and the window — this component only
	// weaves.

	interface Props {
		rows: RunLedgerRow[] | null;
		now: number;
		windowMs: number;
		stale: boolean;
		/** Canonical topic ids lit on the heddle rail; null = all. The weave
		 *  lenses to runs that crossed a lit topic — the selvage still hems
		 *  the whole window, same as the derived lens rail below. */
		selectedTopics?: ReadonlySet<string> | null;
		/** The viewer's "since you looked" anchor (epoch ms) — rows newer
		 *  than it wear the brighter ground (the digest block's successor). */
		newSince?: number | null;
		onCaughtUp?: (() => void) | null;
		/** The set-probed topic faces (`warpGraph.topicFaces`) — one
		 *  assignment for the whole page; the bare hash is the fallback. */
		topicFaces?: Map<string, RunFace>;
		/** The corpus, for the in-place node unfold (his 08-02 steer: a cloth
		 *  item previews where the reader stands — a page redirect costs them
		 *  their place on the way back). Null while loading; the unfold then
		 *  falls back to a plain run-page link. */
		surface?: SurfaceResponse | null;
		/** THE CROSSING (`crossing.ts`): the warp threads in authored order, and
		 *  run id → the ones each run lifted. Same alphabet the pick lane draws —
		 *  same threads, same cells, same width — so a burning pick and the cloth
		 *  line it becomes carry legibly the same strip. Not the same *x*: this
		 *  row wraps, so the strip sits where the row's own content puts it. */
		threads?: string[];
		crossingIndex?: Map<string, string[]>;
	}

	let {
		rows,
		now,
		windowMs,
		stale,
		selectedTopics = null,
		newSince = null,
		onCaughtUp = null,
		topicFaces = new Map<string, RunFace>(),
		surface = null,
		threads = [],
		crossingIndex = new Map()
	}: Props = $props();

	// The lens rail (the dissolution, 2026-08-02). The chips lens the *past
	// inventory*, and the cloth is the past's one object now, so the rail
	// moved here from the loom band. The vocabulary is still derived, never
	// declared (`loomLens.ts`), read off the same window of rows the weave
	// renders. The lens is local view state, like a fold: it slices what
	// this cloth shows, and nothing outside the cloth ever asks about it.
	let windowRows = $derived((rows ?? []).filter((row) => inClothWindow(row, now, windowMs)));
	// The heddle lens: runs that crossed a lit topic (via `crossingIndex`,
	// the run→topics join). Under a real filter an untopiced run is honestly
	// "not this topic" and folds into the count line below the header.
	let topicRows = $derived(
		selectedTopics === null
			? windowRows
			: windowRows.filter((row) =>
					(crossingIndex.get(row.run_id ?? '') ?? []).some((id) => selectedTopics.has(id))
				)
	);
	let lenses = $derived(availableLenses(topicRows));
	let lens = $state<string>(LENS_ALL);
	// A selection can outlive its lens (rows aged out, the vocabulary moved).
	// Reconciling here keeps the weave and the chip row from disagreeing.
	let activeLens = $derived(reconcileLens(lens, lenses));

	// "Show older" (the phone-density pass, 2026-08-02): `CLOTH_ROOT_CAP`
	// bounds the DOM, not the fetch — every root the weave folds already
	// rode `rows` in from the page's one ledger request (`weave.dropped` is
	// the exact count of roots already in hand but uncapped). So lifting
	// the cap costs nothing, network or otherwise: it just renders what's
	// already there. `Infinity` rather than a bigger finite cap because a
	// half-measure ("+40 more") would just relocate the "no way past this"
	// complaint instead of closing it.
	let showOlder = $state(false);
	let rootCap = $derived(showOlder ? Infinity : CLOTH_ROOT_CAP);

	// Day rules, folds, repo chips and the drop count all recompute on the
	// lensed set — the weave *is* the lensed view. The selvage does not: it
	// is the hem of the whole cloth, not of a lens, so it sums the whole
	// window regardless of which chip is lit.
	let weave = $derived(
		rows === null ? null : weaveCloth(applyLens(topicRows, activeLens), now, windowMs, rootCap)
	);
	let days = $derived(weave === null ? null : groupClothDays(weave.trees));
	let selvage = $derived(rows === null ? null : selvageParts(clothSelvage(rows, now, windowMs)));
	// Rows newer than the viewer's anchor — what "caught up" clears.
	let newCount = $derived(
		newSince === null
			? 0
			: windowRows.filter((row) => {
					const endedAt = Date.parse(row.ended_at ?? '');
					return Number.isFinite(endedAt) && endedAt > newSince;
				}).length
	);
	function isNew(line: ClothLine): boolean {
		return newSince !== null && Number.isFinite(line.endedAt) && line.endedAt > newSince;
	}
	// `weave.dropped` reads 0 once `showOlder` lifts the cap (nothing is
	// dropped any more) — so the hem needs the *pre-lift* total, not the
	// live drop count, to know whether it still has anything to say once
	// expanded. `trees.length + dropped` is invariant under the cap: it's
	// every root the fetch produced, capped or not.
	let totalRoots = $derived(weave === null ? 0 : weave.trees.length + weave.dropped);

	// Expansion is local UI state keyed by the root's id — not part of the
	// fetched data, so a re-poll doesn't fold an open brood. The unnamed
	// fold keys by the day instead: one quiet line per day, opened per day.
	let expanded = new SvelteSet<string>();
	let unfolded = new SvelteSet<string>();

	function toggle(set: SvelteSet<string>, id: string) {
		if (set.has(id)) set.delete(id);
		else set.add(id);
	}

	// The in-place unfold: one open node at a time, keyed by line id. A row
	// tap answers with the run's own node right where the reader stands —
	// the same grammar the machine's seam speaks — and the full page stays
	// one link deeper (`RunNodeInline`'s "full node →").
	let openNode = $state<string | null>(null);

	function clothIdentity(line: ClothLine, child: boolean): NodeIdentity {
		return {
			// Empty on purpose: the node's own digest speaks for a closed run's
			// status — same rule the page's selected sheet follows.
			status: '',
			name: line.named ? line.name : (line.runId ?? line.name),
			context: line.repoLabel,
			runner: [line.runnerShell, line.runnerCore].filter(Boolean).join(' · ') || null,
			spawn: child,
			age: line.age,
			mood: null,
			moodGlyph: null,
			moodFrames: null,
			moodRest: null,
			moodPitch: null
		};
	}

	function clothVitals(line: ClothLine): string[] {
		const parts: string[] = [];
		if (line.chips.length > 0) parts.push(line.chips.map((chip) => chip.label).join(' '));
		parts.push(line.duration);
		return parts;
	}
</script>

{#snippet nodeUnfold(line: ClothLine, child: boolean)}
	{#if openNode === line.id && line.href && line.runId}
		{@const digest = surface
			? nodeDigest(runNodeFromSurface(surface, repoRunSlug(line.repoLabel), runIdSlug(line.runId)))
			: null}
		<div
			class="mt-1 mb-1.5 {child ? 'ml-8' : 'ml-4'} max-[480px]:ml-2"
			out:fade={{ duration: 100 }}
		>
			{#if digest?.mirrored}
				<RunNodeInline
					data={surface}
					repoSlug={repoRunSlug(line.repoLabel)}
					runId={runIdSlug(line.runId)}
					href={line.href}
					vitals={clothVitals(line)}
					identity={clothIdentity(line, child)}
				/>
			{:else}
				<!-- No node in the corpus (closed before the weld, or the mirror
				     hasn't landed) — the honest fallback keeps the way through. -->
				<p class="panel px-3 py-2 font-mono text-[10px] text-ink-quiet">
					no run node mirrored for this run —
					<a href={line.href} class="text-amber-300 hover:text-amber-100">open the run page →</a>
				</p>
			{/if}
		</div>
	{/if}
{/snippet}

{#snippet rowBar(line: ClothLine, child: boolean)}
	<!-- The duration bar *is* the row (his 2026-08-02 read: "instead of being the
	     left cell, could be encoded into the whole row line bar itself"). It used
	     to be a 40px cell at the head of the line, which meant the row's one
	     quantitative fact was competing with its name for the reader's first
	     glance and losing — forty pixels is not enough length to compare across
	     thirty rows anyway. As the row's own underline it has the full width to
	     work in, so a day of runs reads as a *shape* before it reads as text,
	     which is what a band of cloth is supposed to do.
	     Width from `loomBarFraction` against the window-wide max (bars compare
	     across days), colour from the thermal-age stops, bare runs dimmed. Strand
	     rows recede: thinner, dimmer, like the band's nested children. -->
	<span
		class="pointer-events-none absolute inset-x-0 bottom-0 block rounded-[1px] bg-stone-900/50 {child
			? 'h-[1px] opacity-70'
			: 'h-[2px]'}"
		aria-hidden="true"
	>
		<span
			class="block h-full rounded-[1px]"
			class:opacity-40={line.bare}
			style={`width: ${(line.barFraction * 100).toFixed(2)}%; background-color: ${line.color}`}
		></span>
	</span>
{/snippet}

{#snippet curatedLine(line: ClothLine, child: boolean)}
	{#if child}
		<span class="shrink-0 text-ink-mute" aria-hidden="true">↳</span>
	{/if}
	<!-- The title wraps whole (min-w-[9ch] + flex-1 + break-words) — same call
	     the warp's headlines took in #978: a second line beats an amputated
	     clause. `min-w-0` used to sit here instead of a real floor: paired
	     with every sibling on this row being `shrink-0`, the name was the
	     *only* flexible box, so the flex algorithm could shrink it to nothing
	     under crowding (narrow phone viewport + duration/chips/toggle all
	     competing for space) — and `break-words` then had to wrap a hyphenated
	     slug like "the-by-species-split" one character per line to fit that
	     near-zero box (the vertical waterfall). `flex-1` lets it claim
	     leftover space like before; `min-w-[9ch]` is the floor that stops the
	     collapse — the row wraps onto a second flex line (`flex-wrap` on the
	     row, below) rather than crushing the name into a column of letters. -->
	<Crossing
		cells={crossingCells(
			threads,
			line.runId ? crossingIndex.get(line.runId) : undefined,
			topicFaces
		)}
	/>
	<!-- The sigils, immediately before the name: the runes transitioned from
	     run ids to topic ids (2026-08-11) — a run wears the topics of the
	     work it did, the same glyph+hue the heddle rail introduces. A run
	     that crossed no topic wears nothing rather than a fabricated mark. -->
	{#if line.runId}
		{@const sigils = (crossingIndex.get(line.runId) ?? []).slice(0, 3)}
		{#if sigils.length > 0}
			<span class="shrink-0 font-mono" aria-hidden="true">
				{#each sigils as topicId (topicId)}
					{@const face = topicFaces.get(topicId) ?? runFace(topicId)}
					<span style={`color: ${face.color}`} title={topicId}>{face.glyph}</span>
				{/each}
			</span>
		{/if}
	{/if}
	{#if line.href}
		<!-- A tap unfolds the node here (his 08-02 steer) — the row stopped
		     being a page redirect that cost the reader their scroll position
		     on the way back. -->
		<button
			type="button"
			class="min-w-[9ch] flex-1 cursor-pointer break-words text-left text-amber-100 hover:text-amber-50"
			class:opacity-60={line.bare}
			aria-expanded={openNode === line.id}
			onclick={() => (openNode = openNode === line.id ? null : line.id)}
		>
			{line.name}
		</button>
	{:else}
		<span class="min-w-[9ch] flex-1 break-words text-stone-200" class:opacity-60={line.bare}
			>{line.name}</span
		>
	{/if}
	<!-- THE FACE IN THREE TENSES piece 3: the run's final mood — the
	     biography half of identity, small, beside the name it belongs to.
	     `MoodChip` (shared with LiveRuns/PickLane/the run node) so this
	     surface can never disagree with the others about what a mood looks
	     like; it already renders nothing when `line.mood` is null, which is
	     both the ordinary case (no mood set) and today's standing one (the
	     ledger doesn't publish a closed run's mood yet — see `cloth.ts`'s
	     `ClothLine.mood` doc and the PR body for the exact backend gap). -->
	<MoodChip face={line.mood} seed={line.id} class="hidden sm:inline" />
	<!-- Repo chip only when this row is off the window's dominant repo —
	     a single-repo window says nothing per row (the whole cloth is that
	     repo). Short name in the row; the full label rides the hover. -->
	{#if line.repoChip}
		<span class="hidden shrink-0 text-[10px] text-ink-mute sm:inline" title={line.repoChip.full}>
			{line.repoChip.short}
		</span>
	{/if}
	{#if line.chips.length > 0}
		<span class="shrink-0 text-stone-300">
			{line.chips.map((chip) => chip.label).join(' ')}
		</span>
	{/if}
	{#if line.items.length > 0}
		<!-- THE WELD (#972): the warp item this run was ignited from — the
		     address is the reference; the item's `taken:` row points back. -->
		<span
			class="hidden shrink-0 font-mono text-[10px] text-amber-300/80 sm:inline"
			title="ignited from the warp"
		>
			🧵 {line.items.join(' · ')}
		</span>
	{/if}
	<!-- Metadata stays `shrink-0` (it must never itself collapse to
	     mush) but rides `ml-auto` inside a `flex-wrap` row (below), so on a
	     narrow phone where the name has already claimed its floor and there
	     is no room left on the line, this drops to its own trailing line
	     instead of squeezing the name further. -->
	<span class="ml-auto shrink-0 text-[10px] whitespace-nowrap text-ink-mute max-[480px]:text-[9px]">
		{line.duration} · {line.age}
	</span>
{/snippet}

{#snippet runRow(tree: ClothTree, index: number)}
	<div role="listitem" in:glitchReveal={{ duration: 240, delay: index * 24 }}>
		<div
			class="relative flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 pb-[3px] font-mono text-xs leading-relaxed max-[480px]:gap-x-1.5 {isNew(
				tree.root
			)
				? 'bg-stone-100/5'
				: ''}"
		>
			{@render rowBar(tree.root, false)}
			{@render curatedLine(tree.root, false)}
			{#if tree.children.length > 0}
				<button
					type="button"
					class="shrink-0 cursor-pointer text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
					aria-expanded={expanded.has(tree.root.id)}
					onclick={() => toggle(expanded, tree.root.id)}
				>
					{expanded.has(tree.root.id) ? '▾' : '▸'}
					{tree.children.length} strand{tree.children.length === 1 ? '' : 's'}
				</button>
			{/if}
		</div>
		{@render nodeUnfold(tree.root, false)}
		{#if expanded.has(tree.root.id)}
			<div class="mt-0.5 space-y-0.5" out:fade={{ duration: 100 }}>
				{#each tree.children as child, childIndex (child.id)}
					<div
						class="relative ml-4 flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 pb-[2px] font-mono text-[11px] leading-relaxed max-[480px]:ml-2 max-[480px]:gap-x-1.5 {isNew(
							child
						)
							? 'bg-stone-100/5'
							: ''}"
						in:glitchReveal={{ duration: 240, delay: childIndex * 24 }}
					>
						{@render rowBar(child, true)}
						{@render curatedLine(child, true)}
					</div>
					{@render nodeUnfold(child, true)}
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

<div class="panel p-4 max-[480px]:p-2.5">
	<div class="mb-3 flex items-center justify-between gap-2 text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">the cloth</span>
		<span class="flex items-center gap-2">
			{#if newCount > 0 && onCaughtUp}
				<!-- The digest block's successor: the anchor lives here now. New
				     rows wear the brighter ground; one press retires the glow. -->
				<button
					type="button"
					class="cursor-pointer border border-stone-700/60 bg-stone-900/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-amber-200 uppercase hover:text-amber-100"
					onclick={() => onCaughtUp?.()}
				>
					{newCount} new · caught up
				</button>
			{/if}
			{#if stale}
				<span
					class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
					>stale report</span
				>
			{/if}
			<span class="font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase">
				past · {loomPastWindowLabel(windowMs)}
			</span>
		</span>
	</div>

	<!-- The lens rail, above the weave it slices. Every chip is derived from
	     the rows in this window — origins from `source_system`, shapes from
	     the relic manifests, the strand stack from `is_subspawn`. It renders
	     even over an empty weave: a lit chip over zero rows must stay
	     un-clickable-off or the reader is trapped in an empty slice. -->
	{#if lenses.length > 1}
		<div
			class="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[9px] leading-none"
			role="group"
			aria-label="lenses over the cloth"
		>
			{#each lenses as candidate (candidate.id)}
				<button
					type="button"
					class="cursor-pointer tracking-[0.08em] uppercase transition-colors"
					class:text-amber-200={activeLens === candidate.id}
					class:text-ink-mute={activeLens !== candidate.id}
					class:hover:text-stone-400={activeLens !== candidate.id}
					aria-pressed={activeLens === candidate.id}
					title={`${candidate.count} run${candidate.count === 1 ? '' : 's'} · ${candidate.facet}`}
					onclick={() => (lens = activeLens === candidate.id ? LENS_ALL : candidate.id)}
				>
					{candidate.label}<span class="ml-1 text-ink-mute">{candidate.count}</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if selectedTopics !== null && windowRows.length > topicRows.length}
		<p class="mb-1 font-mono text-[10px] text-ink-mute">
			{topicRows.length} of {windowRows.length} runs in lit topics
		</p>
	{/if}

	{#if weave === null || days === null || selvage === null}
		<p class="font-mono text-[10px] text-ink-quiet">reading the cloth…</p>
	{:else}
		<!-- The selvage: the cloth's self-finished edge. One quiet row, first.
		     It renders whenever the window holds anything — even under a lens
		     that empties the weave — because it hems the whole cloth, never
		     the lensed slice. -->
		{#if windowRows.length > 0}
			<p
				class="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-stone-800/70 pb-2 font-mono text-[10px] text-ink-quiet"
				aria-label="spend and produce over the window"
			>
				<span class="tracking-[0.16em] text-ink-mute uppercase">selvage</span>
				{#each selvage as part, index (index)}
					<span>{part}</span>
				{/each}
			</p>
		{/if}

		{#if weave.trees.length === 0}
			<!-- An empty weave under an active lens means something different
			     from an empty window — saying "nothing woven" while rows sit
			     one chip away would be the cloth lying about its own contents. -->
			<p class="font-mono text-[10px] text-ink-quiet">
				{activeLens === LENS_ALL
					? 'nothing woven in this window yet.'
					: 'nothing in this window matches this lens.'}
			</p>
		{/if}

		<!-- The weave: root runs day by day, newest day first, one curated
		     line each; a slim quiet rule gives ~30 rows their day rhythm. The
		     past glitches (band grammar), so lines assemble rather than fade. -->
		<div class="space-y-2">
			{#each days as day (day.key)}
				<div>
					<div
						class="mb-1 flex items-center gap-2 font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase"
					>
						<span class="h-px w-4 bg-stone-800/70" aria-hidden="true"></span>
						<span>{day.dayLabel} · {day.runCount} run{day.runCount === 1 ? '' : 's'}</span>
						<span class="h-px min-w-4 flex-1 bg-stone-800/70" aria-hidden="true"></span>
					</div>
					<div class="space-y-0.5" role="list" aria-label={`runs closed on ${day.dayLabel}`}>
						{#each day.trees as tree, index (tree.root.id)}
							{@render runRow(tree, index)}
						{/each}
						{#if day.unnamed}
							<!-- The nameless fold: runs whose only title would be a raw
							     id collapse into one quiet line per day — expandable,
							     never dropped. A named run never lands here. -->
							<div role="listitem">
								<button
									type="button"
									class="flex cursor-pointer items-baseline gap-2 font-mono text-[11px] leading-relaxed text-ink-quiet hover:text-stone-300"
									aria-expanded={unfolded.has(day.key)}
									onclick={() => toggle(unfolded, day.key)}
								>
									<span class="shrink-0" aria-hidden="true">▫</span>
									<span>{day.unnamed.label}</span>
								</button>
								{#if unfolded.has(day.key)}
									<div
										class="mt-0.5 ml-4 space-y-0.5 max-[480px]:ml-2"
										role="list"
										aria-label={`unnamed runs closed on ${day.dayLabel}`}
										out:fade={{ duration: 100 }}
									>
										{#each day.unnamed.trees as tree, index (tree.root.id)}
											{@render runRow(tree, index)}
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>

		<!-- The honest hem: the cap bounds the DOM, never the truth — and now
		     it's a door, not a wall. Every dropped root already rode the same
		     `rows` this weave read from (the ledger fetch caps at
		     `PRODUCE_GAUGE_LEDGER_LIMIT` rows), so lifting `CLOTH_ROOT_CAP`
		     costs no round trip. Past that ceiling there genuinely is no
		     further page to ask for: the endpoint has no `offset`, so "older
		     still" past what this fetch already holds is a real gap, not a
		     rendering one. -->
		{#if totalRoots > CLOTH_ROOT_CAP}
			<p class="mt-2 font-mono text-[10px] text-ink-mute">
				{#if showOlder}
					every root in this window's fetch is shown
				{:else}
					+ {weave.dropped} older in the window ·
					<button
						type="button"
						class="cursor-pointer text-ink-mute underline decoration-dotted hover:text-stone-300"
						onclick={() => (showOlder = true)}
					>
						show older
					</button>
				{/if}
			</p>
		{/if}
	{/if}
</div>
