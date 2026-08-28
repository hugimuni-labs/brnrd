<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { glitchReveal } from './transitions';
	import { environmentDisplay } from './railBench';
	import { fuelProviderGroups } from './fuelProviders';
	import { quotaLevel, type QuotaShell } from './quota';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_SPENT, STATUS_UNKNOWN } from './statusPalette';
	import type { RunnersResponse } from './runners';
	import type { ConnectedRepo, EnvironmentOption } from './repos';
	import { IDLE_ROW, OFF_MARK, OFF_ROW, SELECTED_OPTION } from './stateChrome';

	// THE BENCH (w-68, signed 2026-08-19). Project · environment · core —
	// its own surface, mounted only while the reader asked for it (the
	// gauge's "▸ bench" tap), free to be as tall as it needs because it is
	// no longer sharing a sticky box with the fixed-height gauge. Where the
	// picking happens now; action receipts (a tap's own error/note) live
	// here too, with the control that caused them.
	//
	// design-resident-field.md §"Settings, fuel, and the next dispatch":
	// "press a provider row" opens exactly this surface. `focusProvider` is
	// **the** provider cursor for the whole bench — not a one-shot hint. The
	// Resources readout and SpoolRack's shell tabs are two renderings of it,
	// and `onProviderSelect` is how either of them moves it.
	//
	// Before 2026-08-28 there were two: this prop seeded a `manualShell`
	// `$state` inside SpoolRack once, and a subsequent tab tap moved only
	// that copy. The panel then showed one provider's Resources above
	// another provider's cores, under one heading — the reported defect
	// ("select codex core and see completely unrelated data"). A cursor
	// stored twice is a cursor that will disagree with itself.
	interface Props {
		runners: RunnersResponse | null;
		runnersError?: string | null;
		runnersNote?: string | null;
		repos?: ConnectedRepo[] | null;
		shells?: QuotaShell[] | null;
		focusProvider?: string | null;
		now?: number;
		onTap?: (profileName: string, repoLabel: string | null, environment: string | null) => void;
		onReleaseSticky?: () => void;
		/** Move the bench's one provider cursor — raised by SpoolRack's shell
		 *  tabs, owned by the page, read back through `focusProvider`. */
		onProviderSelect?: (provider: string) => void;
	}

	let {
		runners,
		runnersError = null,
		runnersNote = null,
		repos = null,
		shells = null,
		focusProvider = null,
		now = Date.now(),
		onTap,
		onReleaseSticky,
		onProviderSelect
	}: Props = $props();

	// Every observed meter for the provider under the cursor — "Resources"
	// in the design's own vocabulary. Expanded, every reading gets an equal
	// full-width bar: the collapsed gauge compresses to the binding window
	// because it has 12px; this surface has the room, so it spends it on the
	// breakdown rather than on the same one number again.
	let providerGroups = $derived(fuelProviderGroups(shells ?? []));
	// With no explicit tap yet, the bench still has a provider — whichever
	// one SpoolRack's tabs will land on. Resolving it here rather than
	// rendering nothing keeps Resources and the core list from opening in
	// two different states on the bench's very first paint.
	let resourceGroup = $derived(
		providerGroups.find((group) => group.provider === focusProvider) ?? null
	);
	/** The core-scope allowances for the provider under the cursor, keyed by
	 *  the core they gate. A `fable · week` window constrains the `fable`
	 *  core, never the whole claude shell — so it renders on that core's own
	 *  row in the rack, where the choice it constrains is actually made. */
	let coreAllowances = $derived(
		new Map(
			(resourceGroup?.meters ?? [])
				.filter((meter) => meter.scope === 'core' && meter.coreId !== null)
				.map((meter) => [meter.coreId as string, meter])
		)
	);

	const LEVEL_COLOR: Record<string, string> = {
		burning: STATUS_BURNING,
		cooling: STATUS_COOLING,
		spent: STATUS_SPENT,
		unknown: STATUS_UNKNOWN
	};

	let repoSelection = $state<string | null>(null);
	let environmentSelection = $state<string | null>(null);
	let selectedRepo = $derived(
		(repos ?? []).find(
			(repo) => repo.repo_full_name === (repoSelection ?? runners?.wake_request?.repo_label)
		) ??
			(repos ?? []).find((repo) => repo.dispatch_default) ??
			(repos ?? [])[0]
	);
	let environmentOptions = $derived<EnvironmentOption[]>(selectedRepo?.environments ?? []);
	let environment = $derived(environmentDisplay(selectedRepo, environmentSelection));

	function selectRepo(repo: ConnectedRepo) {
		repoSelection = repo.repo_full_name;
		environmentSelection = null;
	}

	function tapRunner(profileName: string) {
		onTap?.(profileName, selectedRepo?.repo_full_name ?? null, environmentSelection);
	}
</script>

<div
	data-measure="bench"
	class="workbench border-t border-stone-700 bg-stone-950 px-2 py-3 sm:px-5 sm:py-5"
	in:glitchReveal={{ duration: 240 }}
>
	<div data-measure="error-note">
		{#if runnersError}
			<p class="mb-2 text-sm text-red-400">{runnersError}</p>
		{/if}
		{#if runnersNote}
			<p class="mb-2 font-mono text-xs text-amber-300">{runnersNote}</p>
		{/if}
	</div>
	{#if runners === null}
		{#if !runnersError}
			<p class="text-sm text-ink-quiet">Loading…</p>
		{/if}
	{:else}
		<div class="bench-bays mb-3 grid gap-3 md:grid-cols-2 md:gap-5">
			<section data-measure="project" class="bench-bay">
				<div class="workshop-label">project</div>
				{#if repos === null}
					<p class="font-mono text-xs text-ink-quiet">Loading account projects…</p>
				{:else if repos.length === 0}
					<p class="font-mono text-xs text-ink-quiet">No connected projects.</p>
				{:else}
					<div class="space-y-1.5">
						{#each repos as repo (repo.id)}
							{@const selected = selectedRepo?.id === repo.id}
							{@const dispatchable = repo.daemon_status === 'online'}
							<!-- A project without a live daemon cannot take a dispatch;
							     offering it as a selectable target promises a wake nobody
							     will serve (2026-07-22 round). Same off-row grammar as the
							     environment options below — design it off, don't dim it. -->
							<button
								data-role="bench-pick"
								type="button"
								disabled={!dispatchable}
								title={dispatchable
									? `next pick → ${repo.repo_full_name}`
									: `daemon ${repo.daemon_status} — cannot take a pick`}
								onclick={() => selectRepo(repo)}
								class="pick-row flex min-h-11 w-full items-center justify-between gap-4 border px-3 py-2 text-left transition-colors {dispatchable
									? selected
										? SELECTED_OPTION
										: IDLE_ROW
									: OFF_ROW}"
							>
								<span
									class="min-w-0 truncate font-mono text-sm font-medium {!dispatchable
										? 'text-ink-mute'
										: selected
											? 'text-stone-100'
											: 'text-stone-300'}"
								>
									{dispatchable ? '' : OFF_MARK}{repo.repo_full_name}
								</span>
								<span
									class="flex shrink-0 flex-col items-end font-mono text-[10px] leading-tight uppercase sm:flex-row sm:gap-2"
								>
									{#if repo.dispatch_default}<span class="text-sky-300">default</span>{/if}
									<span
										class={repo.daemon_status === 'online' ? 'text-stone-400' : 'text-ink-mute'}
									>
										{repo.daemon_status}
									</span>
								</span>
							</button>
						{/each}
					</div>
				{/if}
			</section>

			<section data-measure="environment" class="bench-bay">
				<div class="workshop-label">environment</div>
				<div class="space-y-1.5">
					<button
						data-role="bench-pick"
						type="button"
						onclick={() => (environmentSelection = null)}
						class="pick-row flex min-h-11 w-full items-center justify-between gap-4 border px-3 py-2 text-left transition-colors {environmentSelection ===
						null
							? SELECTED_OPTION
							: IDLE_ROW}"
					>
						<!-- #1516: the name and the badge render as two elements now,
						     never one string joined by the same `·` the name may
						     already carry internally (`host · default` is a real
						     environment name). -->
						<span class="font-mono text-sm font-medium text-stone-100">{environment.name}</span>
						<span
							class="flex shrink-0 flex-col items-end font-mono text-[10px] leading-tight uppercase sm:flex-row sm:gap-2"
						>
							{#if environment.isDefault}<span class="text-sky-300">default</span>{/if}
							<span class="text-ink-quiet">from repo policy</span>
						</span>
					</button>
					{#each environmentOptions as option (option.name)}
						<button
							data-role="bench-pick"
							type="button"
							disabled={!option.available}
							title={option.reason ?? `next wake in ${option.name}`}
							onclick={() => (environmentSelection = option.name)}
							class="pick-row flex min-h-11 w-full items-center justify-between gap-4 border px-3 py-2 text-left transition-colors {option.available
								? environmentSelection === option.name
									? SELECTED_OPTION
									: IDLE_ROW
								: OFF_ROW}"
						>
							<span
								class="font-mono text-sm font-medium {option.available
									? 'text-stone-300'
									: 'text-ink-mute'}"
							>
								{option.available ? '' : OFF_MARK}{option.name}
							</span>
							{#if !option.available}
								<span class="truncate font-mono text-[10px] text-ink-mute">{option.reason}</span>
							{/if}
						</button>
					{/each}
					{#if environmentOptions.length === 0}
						<p class="px-2 font-mono text-[10px] text-ink-mute">No daemon availability report.</p>
					{/if}
				</div>
			</section>
		</div>
		{#if resourceGroup}
			<!-- RESOURCES — every window this provider reports, one full-width bar
		     each, sitting directly on top of the core list the same cursor
		     drives. Text percentages lived here until 2026-08-28, which had
		     the levels exactly inverted: the 12px collapsed row drew the
		     graphics and this surface, with room to spare, drew the words. -->
			<section data-measure="resources" class="resource-bay mb-3">
				<div class="workshop-label">{resourceGroup.provider} · resources</div>
				{#if resourceGroup.meters.length === 0}
					<p class="font-mono text-xs text-ink-quiet">
						No quota report for {resourceGroup.provider}.
					</p>
				{:else}
					<div class="resource-list">
						{#each resourceGroup.meters as meter (meter.id)}
							{@const level = quotaLevel(meter.percent)}
							{@const binding = meter.id === resourceGroup.primary?.id}
							<div class="resource-row" title={meter.tooltip}>
								<!-- A core allowance keeps its own window name: `fable · week`
								     is a weekly ceiling on one core, and "fable" alone loses which
								     of fable's ceilings this row is reading. -->
								<span class="resource-name" class:is-binding={binding}>
									{meter.scope === 'core'
										? `${meter.coreId} · ${meter.windowName}`
										: meter.windowName}
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
		{/if}
		<div class="spool-bay">
			<SpoolRack
				profiles={runners.profiles}
				defaultProfile={runners.default}
				stale={runners.stale}
				wakeRequest={runners.wake_request ?? null}
				sticky={runners.sticky ?? null}
				{now}
				onTap={tapRunner}
				{onReleaseSticky}
				selectedShell={focusProvider}
				onShellSelect={onProviderSelect}
				{coreAllowances}
			/>
		</div>
	{/if}
</div>

<style>
	.workbench {
		background-image:
			linear-gradient(rgb(255 255 255 / 0.025) 1px, transparent 1px),
			linear-gradient(90deg, rgb(255 255 255 / 0.018) 1px, transparent 1px);
		background-size: 24px 24px;
	}
	.bench-bay {
		min-width: 0;
	}
	.resource-bay {
		min-width: 0;
	}
	.resource-list {
		border-top: 1px solid rgb(68 64 60 / 0.35);
	}
	/* Name · bar · number · note. One axis per row, all four bars sharing a
	   left edge and a scale, so the readings compare by length the way the
	   overlaid stack only pretended to. */
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
	.workshop-label {
		margin-bottom: 0.4rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: rgb(168 162 158);
	}
	.pick-row {
		min-height: 44px;
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
	.spool-bay :global(button[role='tab']),
	.spool-bay :global(button[data-role='rack-row-tap']) {
		min-height: 44px;
	}
	.spool-bay :global(button[data-role='rack-row-tap']) {
		padding-block: 0.6rem;
	}
</style>
