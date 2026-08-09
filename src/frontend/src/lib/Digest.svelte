<script lang="ts">
	import { buildDigest, digestAnchor } from './digest';
	import { ageLabel, type RunLedgerRow } from './runLedger';
	import { boltVerdictLabel } from './bolts';

	// THE DIGEST (design-run-route.md §The home page becomes a map, #1256):
	// replaces the summons strip (`BoltSummons.svelte`, retired) and the
	// cloth-head lane (`Cloth.svelte`, retired) — both were per-run
	// acceptance chips over the same unbounded, ever-growing count. This
	// renders once, at the door: one "since you looked" aggregate line, then
	// only the rows that carry something addressed to the viewer. No
	// per-row TAKE, no counter — glancing is the taking; the one "caught
	// up" press is the whole interaction surface.

	interface Props {
		/** Null while the run-ledger feed hasn't resolved yet — the count
		 *  doctrine (`backchannel.ts`): never render a partial digest. */
		rows: RunLedgerRow[] | null;
		now: number;
		/** The viewer's own last confirmed "caught up" press, or null if
		 *  they have never pressed it — read from `localStorage`, per-viewer,
		 *  the same discipline `bolts.ts` established for ack state. */
		lastLookedAt: number | null;
		onCaughtUp?: () => void;
	}

	let { rows, now, lastLookedAt, onCaughtUp }: Props = $props();

	let since = $derived(digestAnchor(lastLookedAt, now));
	let digest = $derived(rows === null ? null : buildDigest(rows, since, now));
	// The explicit anchor, rendered visibly rather than a guessed cursor
	// (#1256: "prefer 'since <explicit timestamp>' … over a guessed
	// cursor") — the shared relative/absolute grammar every other surface
	// on this dashboard already speaks (`runLedger.ts`'s `ageLabel`).
	let sinceLabel = $derived(ageLabel(since, now));
</script>

{#if digest !== null}
	<div class="subpanel mb-3 px-3 py-2.5 font-mono text-[11px]" role="status">
		<div class="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
			<p class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
				<span class="tracking-[0.14em] text-ink-mute uppercase">since {sinceLabel}</span>
				<!-- `selvageParts` always leads with the run count (`"0 runs"`
				     included) — an honest zero, never an omitted line. -->
				<span class="text-stone-200">{digest.summaryParts.join(' · ')}</span>
			</p>
			<!-- The one action the digest owns (#1256: "at most one 'caught up'
			     press" — no per-row TAKE, no growing counter). Idempotent:
			     pressing it just moves the anchor to now, always safe to tap
			     again. -->
			<button
				type="button"
				class="shrink-0 cursor-pointer text-[10px] tracking-wide text-amber-300 uppercase hover:text-amber-100"
				onclick={() => onCaughtUp?.()}
			>
				caught up
			</button>
		</div>

		{#if digest.rows.length > 0}
			<ul class="mt-2 space-y-1 border-t border-stone-800/70 pt-2" role="list">
				{#each digest.rows as row (row.runId)}
					<li class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
						<a
							href={row.href}
							class="min-w-[9ch] flex-1 break-words text-amber-100 hover:text-amber-50"
						>
							{row.name}
						</a>
						{#if row.bolt === 'annotated'}
							<span class="shrink-0 text-amber-400">{boltVerdictLabel(row.bolt)}</span>
						{/if}
						<span class="shrink-0 text-ink-mute">{ageLabel(row.endedAt, now)}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
{/if}
