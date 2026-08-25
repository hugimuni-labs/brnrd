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
	// "press a provider row" opens exactly this surface. `focusProvider`
	// carries which one, so the Resources readout below and the Next-run
	// picker (SpoolRack, already the Shell/Core selector) both open already
	// pointed at it — the resource reading informs the choice without
	// becoming a second choice mechanism (no new POST: `SpoolRack`'s own
	// tap-to-request machinery is untouched).
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
		onReleaseSticky
	}: Props = $props();

	// Every observed meter for the focused provider — "Resources" in the
	// design's own vocabulary: window, remaining, reset age, one row each,
	// no ghost/primary distinction here (that compression is the collapsed
	// gauge's job; expanded, every reading is equally readable).
	let resourceGroup = $derived(
		focusProvider
			? (fuelProviderGroups(shells ?? []).find((group) => group.provider === focusProvider) ?? null)
			: null
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
	{#if resourceGroup}
		<!-- Resources: every meter this provider reports, expanded — the
		     fuel design's first half of "press a provider row". Sits above
		     the shell/core picker below, which the same tap already opened
		     to this provider's tab, so the reading and the choice it informs
		     share one view. -->
		<section data-measure="resources" class="resource-bay mb-3">
			<div class="workshop-label">{resourceGroup.provider} · resources</div>
			{#if resourceGroup.meters.length === 0}
				<p class="font-mono text-xs text-ink-quiet">
					No quota report for {resourceGroup.provider}.
				</p>
			{:else}
				<div class="space-y-1">
					{#each resourceGroup.meters as meter (meter.id)}
						{@const level = quotaLevel(meter.percent)}
						<div
							class="resource-row flex items-baseline justify-between gap-3 border border-stone-800/60 bg-stone-900/40 px-2 py-1.5"
							title={meter.tooltip}
						>
							<span class="flex min-w-0 items-baseline gap-2 font-mono text-xs">
								<span class="text-stone-300">{meter.label}</span>
								{#if meter.scope === 'core'}
									<span class="text-[10px] tracking-wide text-ink-quiet uppercase"
										>core allowance</span
									>
								{/if}
							</span>
							<span class="flex items-baseline gap-2 font-mono text-[11px]">
								<strong style={`color: ${LEVEL_COLOR[level]}`}
									>{meter.percent === null ? '?' : `${Math.round(meter.percent)}%`}</strong
								>
								{#if meter.resetShort}<span class="text-ink-quiet">↻{meter.resetShort}</span>{/if}
							</span>
						</div>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
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
				focusShell={focusProvider}
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
	.resource-row {
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
