<script lang="ts">
	import { glitchReveal } from './transitions';
	import type { ScheduledWake } from './scheduledWakes';
	import { futureShelfRows } from './futureShelf';
	import { statusDotStyle } from './statusPalette';

	// The future's one object (the dissolution, 2026-08-02): the scheduled
	// wakes with their ETA bars, factored out of the loom band and rendered
	// by the rack — where "next wake" already lives. Content and behaviour
	// travel intact from the band's future shelf: soonest first, the compact
	// ETA legend, frost thawing to amber as the fire nears, and the band's
	// sqrt bar fraction (all computed in `futureShelf.ts`). A tap still
	// reports the wake to the page, and the detail sheet still answers.

	interface Props {
		scheduledWakes: ScheduledWake[] | null;
		now: number;
		/** Selection is the page's: the shelf reports, the detail sheet answers. */
		onSelect?: (kind: 'run' | 'wake', id: string) => void;
		selectedId?: string | null;
	}

	let { scheduledWakes, now, onSelect, selectedId = null }: Props = $props();

	let rows = $derived(futureShelfRows(scheduledWakes, now));
</script>

<div aria-label="scheduled wakes">
	<div
		class="mb-1 flex items-baseline justify-between font-mono text-[9px] tracking-[0.16em] text-ink-mute uppercase"
	>
		<span>future</span>
		{#if rows.length > 0}
			<span class="normal-case">{rows.length} scheduled</span>
		{/if}
	</div>
	{#if scheduledWakes !== null && rows.length === 0}
		<p class="font-mono text-[9px] text-ink-mute">nothing queued</p>
	{:else}
		<div class="flex flex-col gap-px">
			{#each rows as row, index (row.wake.id)}
				<button
					type="button"
					class="flex h-[18px] w-full cursor-pointer items-center justify-start gap-1.5"
					style={`color: ${row.color};${selectedId === row.wake.id ? ' filter: brightness(1.6);' : ''}`}
					title={row.legend}
					onclick={() => onSelect?.('wake', row.wake.id)}
					in:glitchReveal={{ duration: 240, delay: 70 + index * 26 }}
				>
					<span
						class="h-2 w-2 shrink-0 rounded-full"
						style={statusDotStyle('burning', row.color, row.urgency)}
						aria-hidden="true"
					></span>
					<span
						class="h-[7px] shrink-0 rounded-r-[1px]"
						style={`width: ${(row.barFraction * 38).toFixed(2)}%; background-color: ${row.color}`}
						aria-hidden="true"
					></span>
					<span class="truncate font-mono text-[9px] leading-none whitespace-nowrap">
						{row.legend}
					</span>
				</button>
			{/each}
		</div>
	{/if}
</div>
