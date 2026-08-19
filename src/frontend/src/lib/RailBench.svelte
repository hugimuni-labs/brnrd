<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { glitchReveal } from './transitions';
	import { environmentDisplay } from './railBench';
	import type { RunnersResponse } from './runners';
	import type { ConnectedRepo, EnvironmentOption } from './repos';
	import { IDLE_ROW, OFF_MARK, OFF_ROW, SELECTED_OPTION } from './stateChrome';

	// THE BENCH (w-68, signed 2026-08-19). Project · environment · core —
	// its own surface, mounted only while the reader asked for it (the
	// gauge's "▸ bench" tap), free to be as tall as it needs because it is
	// no longer sharing a sticky box with the fixed-height gauge. Where the
	// picking happens now; action receipts (a tap's own error/note) live
	// here too, with the control that caused them.
	interface Props {
		runners: RunnersResponse | null;
		runnersError?: string | null;
		runnersNote?: string | null;
		repos?: ConnectedRepo[] | null;
		now?: number;
		onTap?: (profileName: string, repoLabel: string | null, environment: string | null) => void;
		onReleaseSticky?: () => void;
	}

	let {
		runners,
		runnersError = null,
		runnersNote = null,
		repos = null,
		now = Date.now(),
		onTap,
		onReleaseSticky
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
	}

	function tapRunner(profileName: string) {
		onTap?.(profileName, selectedRepo?.repo_full_name ?? null, environmentSelection);
	}
</script>

<div
	data-measure="bench"
	class="panel border-t border-stone-800/70 p-3"
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
		<div class="mb-3 grid gap-3 lg:grid-cols-2">
			<div data-measure="project" class="panel p-4">
				<div class="mb-3 font-mono text-sm font-medium tracking-wide text-amber-200 uppercase">
					project
				</div>
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
								type="button"
								disabled={!dispatchable}
								title={dispatchable
									? `next pick → ${repo.repo_full_name}`
									: `daemon ${repo.daemon_status} — cannot take a pick`}
								onclick={() => selectRepo(repo)}
								class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {dispatchable
									? selected
										? SELECTED_OPTION
										: IDLE_ROW
									: OFF_ROW}"
							>
								<span
									class="truncate font-mono text-xs {!dispatchable
										? 'text-ink-mute'
										: selected
											? 'text-stone-100'
											: 'text-stone-300'}"
								>
									{dispatchable ? '' : OFF_MARK}{repo.repo_full_name}
								</span>
								<span class="flex shrink-0 items-baseline gap-2 font-mono text-[10px] uppercase">
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
			</div>

			<div data-measure="environment" class="panel p-4">
				<div class="mb-3 font-mono text-sm font-medium tracking-wide text-amber-200 uppercase">
					environment
				</div>
				<div class="space-y-1.5">
					<button
						type="button"
						onclick={() => (environmentSelection = null)}
						class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {environmentSelection ===
						null
							? SELECTED_OPTION
							: IDLE_ROW}"
					>
						<!-- #1516: the name and the badge render as two elements now,
						     never one string joined by the same `·` the name may
						     already carry internally (`host · default` is a real
						     environment name). -->
						<span class="font-mono text-xs text-stone-100">{environment.name}</span>
						<span class="flex shrink-0 items-baseline gap-2 font-mono text-[10px] uppercase">
							{#if environment.isDefault}<span class="text-sky-300">default</span>{/if}
							<span class="text-ink-quiet">from repo policy</span>
						</span>
					</button>
					{#each environmentOptions as option (option.name)}
						<button
							type="button"
							disabled={!option.available}
							title={option.reason ?? `next wake in ${option.name}`}
							onclick={() => (environmentSelection = option.name)}
							class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {option.available
								? environmentSelection === option.name
									? SELECTED_OPTION
									: IDLE_ROW
								: OFF_ROW}"
						>
							<span
								class="font-mono text-xs {option.available ? 'text-stone-300' : 'text-ink-mute'}"
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
			</div>
		</div>
		<SpoolRack
			profiles={runners.profiles}
			defaultProfile={runners.default}
			stale={runners.stale}
			wakeRequest={runners.wake_request ?? null}
			sticky={runners.sticky ?? null}
			{now}
			onTap={tapRunner}
			{onReleaseSticky}
		/>
	{/if}
</div>
