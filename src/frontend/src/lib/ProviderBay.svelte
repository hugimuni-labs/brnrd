<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { glitchReveal } from './transitions';
	import { quotaLevel } from './quota';
	import type { FuelMeter, FuelProviderGroup } from './fuelProviders';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_SPENT, STATUS_UNKNOWN } from './statusPalette';
	import type { RunnersResponse } from './runners';

	// THE PRESSED PROVIDER. One provider's readings and one provider's cores,
	// as a single object, opened by pressing that provider's fuel row —
	// "the fuel bars would be clearly pressable, and they would contain the
	// core/shell selection" (maintainer, 2026-08-28).
	//
	// The structural win is what is *absent*. Until this existed, the panel
	// held a `CLAUDE | CODEX` tab strip beside a Resources heading driven by
	// a different value, so a provider could be selected in two places and
	// they drifted apart — a codex core list under a claude heading. #1671
	// synchronised the two cursors. This deletes the second control: the
	// disclosure state *is* the selection, so there is nowhere for a rival
	// cursor to live.
	//
	// It renders **below** the sticky gauge, never inside it. `.fuel-deck` is
	// a fixed 85px with an acceptance test pinning that twelve providers stay
	// twelve rows; an expansion that grew the box would re-open the exact
	// defect that height exists to prevent.
	interface Props {
		group: FuelProviderGroup;
		runners: RunnersResponse;
		now?: number;
		onTap?: (profileName: string) => void;
		onReleaseSticky?: () => void;
	}

	let { group, runners, now = Date.now(), onTap, onReleaseSticky }: Props = $props();

	const LEVEL_COLOR: Record<string, string> = {
		burning: STATUS_BURNING,
		cooling: STATUS_COOLING,
		spent: STATUS_SPENT,
		unknown: STATUS_UNKNOWN
	};

	/** Core-scope allowances keyed by the core they gate. A `fable · week`
	 *  window constrains the fable core, never the whole claude shell — so it
	 *  rides that core's own row in the rack, where the choice it constrains
	 *  is actually made, and never the shell's bar. */
	let coreAllowances = $derived(
		new Map<string, FuelMeter>(
			group.meters
				.filter((meter) => meter.scope === 'core' && meter.coreId !== null)
				.map((meter) => [meter.coreId as string, meter])
		)
	);
</script>

<div
	data-measure="provider-bay"
	class="provider-bay border-t border-stone-700 bg-stone-950 px-2 py-3 sm:px-5 sm:py-4"
	in:glitchReveal={{ duration: 240 }}
>
	<!-- Every window this provider reports, one full-width bar each, on one
	     shared scale. The collapsed row compresses to the binding window
	     because it has 12px; this surface has the room, so it spends it on
	     the breakdown rather than on the same one number again. -->
	<section data-measure="resources" class="resource-bay mb-3">
		<div class="workshop-label">{group.provider} · windows</div>
		{#if group.meters.length === 0}
			<p class="font-mono text-xs text-ink-quiet">No quota report for {group.provider}.</p>
		{:else}
			<div class="resource-list">
				{#each group.meters as meter (meter.id)}
					{@const level = quotaLevel(meter.percent)}
					{@const binding = meter.id === group.primary?.id}
					<div class="resource-row" title={meter.tooltip}>
						<!-- A core allowance keeps its own window name: `fable · week`
						     is a weekly ceiling on one core, and "fable" alone loses
						     which of fable's ceilings this row is reading. -->
						<span class="resource-name" class:is-binding={binding}>
							{meter.scope === 'core' ? `${meter.coreId} · ${meter.windowName}` : meter.windowName}
						</span>
						<span
							class="resource-track"
							role="img"
							aria-label={`${meter.label}: ${
								meter.percent === null ? 'unknown' : `${Math.round(meter.percent)}% left`
							}`}
						>
							{#if meter.percent !== null}
								<span
									class="resource-fill"
									style={`width: ${meter.percent}%; color: ${LEVEL_COLOR[level]}`}
								></span>
							{/if}
						</span>
						<strong class="resource-percent" style={`color: ${LEVEL_COLOR[level]}`}>
							{meter.percent === null ? '?' : `${Math.round(meter.percent)}%`}
						</strong>
						<span class="resource-note">
							{#if meter.resetShort}<span class="resource-reset">↻{meter.resetShort}</span>{/if}
							{#if binding}
								<span class="resource-tag is-binding" title="the ceiling that stops a run first"
									>binding</span
								>
							{:else if meter.scope === 'core'}
								<span class="resource-tag" title="gates this core only, not the whole shell"
									>core allowance</span
								>
							{/if}
						</span>
					</div>
				{/each}
			</div>
		{/if}
	</section>
	<div class="spool-bay">
		<SpoolRack
			profiles={runners.profiles}
			defaultProfile={runners.default}
			stale={runners.stale}
			wakeRequest={runners.wake_request ?? null}
			sticky={runners.sticky ?? null}
			{now}
			{onTap}
			{onReleaseSticky}
			shell={group.provider}
			{coreAllowances}
		/>
	</div>
</div>

<style>
	.provider-bay {
		background-image:
			linear-gradient(rgb(255 255 255 / 0.025) 1px, transparent 1px),
			linear-gradient(90deg, rgb(255 255 255 / 0.018) 1px, transparent 1px);
		background-size: 24px 24px;
	}
	.resource-bay {
		min-width: 0;
	}
	.workshop-label {
		margin-bottom: 0.4rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: rgb(168 162 158);
	}
	.resource-list {
		border-top: 1px solid rgb(68 64 60 / 0.35);
	}
	/* Name · bar · number · note. One axis per row, every bar sharing a left
	   edge and a scale, so the readings compare by length the way the
	   overlaid stack this replaced only pretended to. */
	.resource-row {
		display: grid;
		min-width: 0;
		grid-template-columns: minmax(0, 6.5rem) minmax(0, 1fr) 2.75rem;
		grid-template-areas:
			'name track pct'
			'note note note';
		align-items: center;
		column-gap: 0.7rem;
		row-gap: 2px;
		border-bottom: 1px solid rgb(68 64 60 / 0.35);
		padding: 0.5rem 0;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	@media (min-width: 768px) {
		.resource-row {
			grid-template-columns: minmax(0, 7.5rem) minmax(0, 1fr) 2.75rem minmax(0, 9.5rem);
			grid-template-areas: 'name track pct note';
			row-gap: 0;
		}
	}
	.resource-name {
		grid-area: name;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.8rem;
		color: rgb(168 162 158);
	}
	.resource-name.is-binding {
		color: rgb(231 229 228);
	}
	.resource-track {
		position: relative;
		grid-area: track;
		display: block;
		height: 8px;
		background: rgb(41 37 36);
		box-shadow: inset 0 0 0 1px rgb(68 64 60 / 0.45);
	}
	.resource-fill {
		position: absolute;
		inset: 0 auto 0 0;
		display: block;
		background: currentColor;
		box-shadow: 0 0 7px currentColor;
		transition: width 500ms ease-out;
	}
	.resource-percent {
		grid-area: pct;
		font-size: 0.75rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		text-align: right;
	}
	.resource-note {
		grid-area: note;
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		overflow: hidden;
		font-size: 0.625rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		color: rgb(120 113 108);
	}
	@media (min-width: 768px) {
		.resource-note {
			justify-content: flex-end;
		}
	}
	.resource-reset {
		flex: none;
	}
	.resource-tag {
		flex: none;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: rgb(87 83 78);
	}
	.resource-tag.is-binding {
		color: rgb(168 162 158);
	}
	.spool-bay :global([data-measure='spool-rack']) {
		border: 0;
		border-top: 1px solid rgb(68 64 60 / 0.7);
		background: transparent;
		box-shadow: none;
		padding: 0.75rem 0 0;
	}
	.spool-bay :global([data-measure='spool-rack'] > div:first-child) {
		min-height: 1.5rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		color: rgb(214 211 209);
	}
	.spool-bay :global(button[data-role='rack-row-tap']) {
		min-height: 44px;
		padding-block: 0.6rem;
	}
</style>
