<script lang="ts">
	import {
		boltCardSections,
		boltVerdictLabel,
		type BoltDeclarationValue,
		type BoltState
	} from './bolts';
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
	// defect class): the design's own mockup shows five sections — verdict,
	// asks ledger, produce, owed, spend — plus `decisions` once #1255 asked
	// for it too. #1236 threaded the validated declaration (asks, owed,
	// decisions, the declared spend estimate, the daemon's own dissent) all
	// the way to this row's `bolt_declaration` column (`bolts.ts`'s module
	// note has the wire's history); every section below is data-driven off
	// it. Absent ≠ empty stays the rule a row that predates the wire
	// (`declaration` null) renders the old labeled-absence wording; a row
	// that declared and carried zero rows says so honestly instead.

	interface Props {
		bolt: BoltState;
		relics: RelicRecord[];
		wallClockSeconds: number | null;
		tokensInput: number | null;
		tokensOutput: number | null;
		usdSubscriptionAttributed: number | null;
		usdCreditsEquivalent: number | null;
		declaration?: BoltDeclarationValue;
	}

	let {
		bolt,
		relics,
		wallClockSeconds,
		tokensInput,
		tokensOutput,
		usdSubscriptionAttributed,
		usdCreditsEquivalent,
		declaration = null
	}: Props = $props();

	let sections = $derived(
		boltCardSections(
			{
				relics,
				wallClockSeconds,
				tokensInput,
				tokensOutput,
				usdSubscriptionAttributed,
				usdCreditsEquivalent
			},
			declaration
		)
	);
	let families = $derived(groupRelicFamilies(relics));
	// Only ever real content when `declaration` is the full shape (not the
	// `{omitted}` marker and not `null`) — the sections below already gate
	// each render on `sections.*`, this is just where the typed narrowing
	// lives so the markup can read `decl.asks` etc. without repeating it.
	let decl = $derived(declaration && !('omitted' in declaration) ? declaration : null);
	let omittedReason = $derived(declaration && 'omitted' in declaration ? declaration.reason : null);
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
			{#if decl && decl.dissent.length > 0}
				<ul class="mt-0.5 space-y-0.5 text-ink-mute">
					{#each decl.dissent as line, i (i)}
						<li>· {line}</li>
					{/each}
				</ul>
			{:else if omittedReason}
				<p class="mt-0.5 text-ink-mute">dissent text not carried — {omittedReason}</p>
			{:else}
				<p class="mt-0.5 text-ink-mute">
					this row predates the daemon's dissent wire (#1236) — not carried here
				</p>
			{/if}
		{/if}
	</div>

	<!-- 2. Asks ledger -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">asks ledger</p>
		{#if sections.asks === 'present' && decl}
			<ul class="mt-0.5 space-y-1">
				{#each decl.asks as ask, i (i)}
					<li class="flex min-w-0 items-baseline gap-1.5">
						<span class="shrink-0 text-stone-300">{ask.disposition}</span>
						<span class="truncate text-ink-quiet">{ask.label || ask.event}</span>
					</li>
				{/each}
			</ul>
		{:else if sections.asks === 'empty'}
			<p class="mt-0.5 text-ink-mute">no asks declared</p>
		{:else if sections.asks === 'omitted'}
			<p class="mt-0.5 text-ink-mute">declaration too large to persist — {omittedReason}</p>
		{:else}
			<p class="mt-0.5 text-ink-mute">
				declaration not carried — no row persists per-ask disposition
			</p>
		{/if}
	</div>

	<!-- 3. Decisions (#1255) -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">decisions</p>
		{#if sections.decisions === 'present' && decl}
			<ul class="mt-0.5 space-y-0.5">
				{#each decl.decisions as line, i (i)}
					<li class="text-stone-300">· {line}</li>
				{/each}
			</ul>
		{:else if sections.decisions === 'empty'}
			<p class="mt-0.5 text-ink-mute">no decisions declared</p>
		{:else if sections.decisions === 'omitted'}
			<p class="mt-0.5 text-ink-mute">declaration too large to persist — {omittedReason}</p>
		{:else}
			<p class="mt-0.5 text-ink-mute">declaration not carried — no row persists decisions</p>
		{/if}
	</div>

	<!-- 4. Produce -->
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

	<!-- 5. Owed -->
	<div>
		<p class="text-[10px] tracking-[0.14em] text-ink-quiet uppercase">owed</p>
		{#if sections.owed === 'present' && decl}
			<ul class="mt-0.5 space-y-1">
				{#each decl.owed as owed, i (i)}
					<li class="text-stone-300">
						· {owed.label ? `${owed.label}: ` : ''}{owed.ref} — {owed.why}{owed.where
							? ` (→ ${owed.where})`
							: ''}
					</li>
				{/each}
			</ul>
		{:else if sections.owed === 'empty'}
			<p class="mt-0.5 text-ink-mute">owed: none</p>
		{:else if sections.owed === 'omitted'}
			<p class="mt-0.5 text-ink-mute">declaration too large to persist — {omittedReason}</p>
		{:else}
			<p class="mt-0.5 text-ink-mute">
				declaration not carried — no row persists a carried-owed line
			</p>
		{/if}
	</div>

	<!-- 6. Spend — measured stamp, plus the resident's declared estimate
	     alongside it when the row carries one (design doc §Spend: "the diff
	     is itself information"). -->
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
		{:else}
			<p class="mt-0.5 text-ink-mute">no measured spend on this row</p>
		{/if}
		{#if sections.spendDeclared === 'present' && decl}
			<p class="mt-0.5 text-ink-quiet">declared: {decl.spendDeclared}</p>
		{:else if sections.spendDeclared === 'empty'}
			<p class="mt-0.5 text-ink-mute">no spend estimate declared</p>
		{:else if sections.spendDeclared === 'omitted'}
			<p class="mt-0.5 text-ink-mute">declared estimate too large to persist — {omittedReason}</p>
		{:else}
			<p class="mt-0.5 text-ink-mute">
				declared estimate not carried — the ledger keeps the measured figure only
			</p>
		{/if}
	</div>
</div>
