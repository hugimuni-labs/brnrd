<script lang="ts">
	import { glitchReveal } from './transitions';
	import { environmentDisplay } from './railBench';
	import type { RunnersResponse } from './runners';
	import type { ConnectedRepo, EnvironmentOption } from './repos';
	import { IDLE_ROW, OFF_MARK, OFF_ROW, SELECTED_OPTION } from './stateChrome';

	// SETTINGS — project · environment, and nothing else.
	//
	// This was THE BENCH: project + environment + a provider's Resources + a
	// `CLAUDE | CODEX` core picker, four things under one heading, reachable
	// through two different entry points that opened slightly different
	// content. Two of those four belonged to a provider and two did not,
	// which is why the panel never read as one object.
	//
	// The provider half moved out to `ProviderBay`, opened by pressing that
	// provider's fuel row (maintainer, 2026-08-28: "move the bench dropdown
	// and the settings out of it… the fuel bars would be clearly pressable,
	// and they would contain the core/shell selection"). What is left here is
	// the pair that is true regardless of which body runs next: *where* the
	// work happens. Action receipts stay with the controls that cause them.
	interface Props {
		runners: RunnersResponse | null;
		runnersError?: string | null;
		runnersNote?: string | null;
		repos?: ConnectedRepo[] | null;
		/** Raised whenever the reader changes *where* the next wake runs, so
		 *  the page can hand the pair to whichever core row is tapped in the
		 *  provider bay — which lives outside this component now. */
		onPlaceChange?: (repoLabel: string | null, environment: string | null) => void;
	}

	let {
		runners,
		runnersError = null,
		runnersNote = null,
		repos = null,
		onPlaceChange
	}: Props = $props();

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
		announce();
	}

	function selectEnvironment(name: string | null) {
		environmentSelection = name;
		announce();
	}

	// The place a wake lands is chosen here and *used* one component over, on
	// whichever core row gets tapped. Announcing on change rather than
	// reaching across keeps that a one-way flow: this block never learns what
	// the rack does with the pair.
	function announce() {
		onPlaceChange?.(selectedRepo?.repo_full_name ?? null, environmentSelection);
	}
</script>

<div
	data-measure="settings"
	class="settings-block border-t border-stone-700 bg-stone-950 px-2 py-3 sm:px-5 sm:py-5"
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
						onclick={() => selectEnvironment(null)}
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
							onclick={() => selectEnvironment(option.name)}
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
	{/if}
</div>

<style>
	.settings-block {
		background-image:
			linear-gradient(rgb(255 255 255 / 0.025) 1px, transparent 1px),
			linear-gradient(90deg, rgb(255 255 255 / 0.018) 1px, transparent 1px);
		background-size: 24px 24px;
	}
	.bench-bay {
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
</style>
