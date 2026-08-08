<script lang="ts">
	import { boltCardSections, boltVerdictLabel, type BoltState } from './bolts';
	import {
		durationLabel,
		familySuffix,
		groupRelicFamilies,
		relicIcon,
		relicLabel,
		tokenLabel,
		usdLabel,
		type RelicRecord
	} from './runLedger';

	// The completion card — the bolt's render (design-the-bolt.md §The
	// completion card, added 2026-08-08). The maintainer's reference is a
	// Zachtronics level-completion card: information carefully prepared so
	// the whole run's accomplishment reads in one place. TAKE stays the
	// claim control on the row this expands from; this component only
	// renders, never acts.
	//
	// Data honesty (the constraint the spec names as the account's dominant
	// defect class): the design's own mockup shows five sections —
	// verdict, asks ledger, produce, owed, spend — as if all five arrive on
	// the wire. The wire audit (`bolts.ts`'s module note, and the report at
	// the declared path) found only two do: the verdict flag itself
	// (`"accepted" | "annotated"`, nothing about *why* when annotated) and
	// produce/spend, which were already ledger columns. `asks`, `decisions`,
	// and `owed` are parsed by `cut_verb.py` at declare-time but never
	// persisted past the daemon's own validation pass — no row, ever, carries
	// them here. Those sections render as a labeled absence, not a guess and
	// not a skip.

	interface Props {
		bolt: BoltState;
		relics: RelicRecord[];
		wallClockSeconds: number | null;
		tokensInput: number | null;
		tokensOutput: number | null;
		usdSubscriptionAttributed: number | null;
		usdCreditsEquivalent: number | null;
	}

	let {
		bolt,
		relics,
		wallClockSeconds,
		tokensInput,
		tokensOutput,
		usdSubscriptionAttributed,
		usdCreditsEquivalent
	}: Props = $props();

	let sections = $derived(
		boltCardSections({
			relics,
			wallClockSeconds,
			tokensInput,
			tokensOutput,
			usdSubscriptionAttributed,
			usdCreditsEquivalent
		})
	);
	let families = $derived(groupRelicFamilies(relics));
</script>

<div
	class="mt-2 space-y-3 border-t border-amber-900/30 pt-2.5 font-mono text-[11px]"
	data-testid="bolt-completion-card"
>
	<!-- 1. Verdict head -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">verdict</p>
		<p class="mt-0.5 text-amber-100">
			{bolt === 'annotated' ? '◐' : '✓'}
			{boltVerdictLabel(bolt)}
		</p>
		{#if bolt === 'annotated'}
			<p class="mt-0.5 text-ink-mute">
				the daemon's dissent text is delivered once, on the closeout reply — it is not carried on
				this row's data, so it cannot be replayed here.
			</p>
		{/if}
	</div>

	<!-- 2. Asks ledger — unconditionally absent on the wire, see module note -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">asks ledger</p>
		<p class="mt-0.5 text-ink-mute">
			declaration not carried — no row persists per-ask disposition
		</p>
	</div>

	<!-- 3. Produce -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">produce</p>
		{#if sections.produce === 'present'}
			<ul class="mt-0.5 space-y-1">
				{#each families as fam, i (i)}
					<li class="flex min-w-0 items-center gap-1.5">
						<span class="shrink-0" title={fam.head.kind}>{relicIcon(fam.head.kind)}</span>
						{#if fam.head.url}
							<a
								href={String(fam.head.url)}
								target="_blank"
								rel="external noreferrer"
								class="truncate text-sky-300 underline decoration-sky-800 hover:text-sky-200"
								>{relicLabel(fam.head)}</a
							>
						{:else}
							<span class="truncate text-stone-300">{relicLabel(fam.head)}</span>
						{/if}
						{#if familySuffix(fam)}
							<span class="shrink-0 text-ink-quiet">{familySuffix(fam)}</span>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="mt-0.5 text-ink-mute">nothing produced this run</p>
		{/if}
	</div>

	<!-- 4. Owed — unconditionally absent on the wire, see module note -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">owed</p>
		<p class="mt-0.5 text-ink-mute">
			declaration not carried — no row persists a carried-owed line
		</p>
	</div>

	<!-- 5. Spend — measured stamp only; the resident's declared estimate is
	     parsed at cut-time but never persisted past validation, so there is
	     nothing to diverge against here. -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">spend</p>
		{#if sections.spend === 'present'}
			<p class="mt-0.5 text-stone-300">
				measured: {durationLabel(wallClockSeconds)} · {tokenLabel(tokensInput)}/{tokenLabel(
					tokensOutput
				)} tok · {usdSubscriptionAttributed !== null
					? usdLabel(usdSubscriptionAttributed)
					: usdLabel(usdCreditsEquivalent)}
			</p>
			<p class="mt-0.5 text-ink-mute">
				declared estimate not carried — the ledger keeps the measured figure only
			</p>
		{:else}
			<p class="mt-0.5 text-ink-mute">no measured spend on this row</p>
		{/if}
	</div>
</div>
