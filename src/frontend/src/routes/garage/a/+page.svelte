<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { environmentDisplay } from '$lib/railBench';
	import { fetchLiveRuns, liveRunDisplayName, type LiveRun } from '$lib/liveRuns';
	import { fetchQuota, type QuotaShell } from '$lib/quota';
	import { fetchRepos, type ConnectedRepo } from '$lib/repos';
	import { fetchRunners, requestWake, type RunnersResponse } from '$lib/runners';
	import { fetchScheduledWakes, wakeTimingText, type ScheduledWake } from '$lib/scheduledWakes';
	import { garageSpring } from '$lib/transitions';
	import {
		compactPercent,
		dispatcherRun,
		handsFor,
		nextProfile,
		runSeconds,
		shellRows
	} from '$lib/garage/sketchA';

	const POLL_MS = 2_000;
	let now = $state(Date.now());
	let runs = $state<LiveRun[]>([]);
	let runners = $state<RunnersResponse | null>(null);
	let shells = $state<QuotaShell[]>([]);
	let repos = $state<ConnectedRepo[]>([]);
	let wakes = $state<ScheduledWake[]>([]);
	let expandedShell = $state<string | null>(null);
	let projectOpen = $state(false);
	let collapsed = $state(false);
	let note = $state<string | null>(null);
	let loading = $state(true);
	let refreshBusy = false;

	let current = $derived(dispatcherRun(runs));
	let hands = $derived(handsFor(runs, current));
	let next = $derived(nextProfile(runners, now));
	let rows = $derived(shellRows(runners?.profiles ?? [], shells, runs, current));
	let selectedRepo = $derived(
		repos.find((repo) => repo.repo_full_name === runners?.wake_request?.repo_label) ??
			repos.find((repo) => repo.dispatch_default) ??
			repos[0] ??
			null
	);
	let environment = $derived(
		environmentDisplay(selectedRepo, runners?.wake_request?.environment ?? null)
	);
	let nextWake = $derived(
		[...wakes]
			.filter((wake) => wake.scheduled_for)
			.sort((a, b) => Date.parse(a.scheduled_for ?? '') - Date.parse(b.scheduled_for ?? ''))[0] ??
			null
	);
	let nextFuel = $derived(rows.find((row) => row.shell === next?.shell)?.fuel ?? null);

	async function refresh() {
		if (refreshBusy) return;
		refreshBusy = true;
		try {
			const [liveData, runnersData, quotaData, repoData, wakeData] = await Promise.all([
				fetchLiveRuns(),
				fetchRunners(),
				fetchQuota(),
				fetchRepos(),
				fetchScheduledWakes()
			]);
			runs = liveData.runs;
			runners = runnersData;
			shells = quotaData.runner_quotas;
			repos = repoData.connected_repos;
			wakes = wakeData.rows;
			note = null;
		} catch (error) {
			note = error instanceof Error ? error.message : 'garage feed unavailable';
		} finally {
			loading = false;
			refreshBusy = false;
		}
	}

	async function choose(profileName: string) {
		try {
			const wake = await requestWake(profileName, {
				repo_label: selectedRepo?.repo_full_name ?? null,
				environment: runners?.wake_request?.environment ?? null
			});
			if (runners) runners = { ...runners, wake_request: wake };
			expandedShell = null;
			note = `next run → ${profileName}`;
		} catch (error) {
			note = error instanceof Error ? error.message : 'next-run claim failed';
		}
	}

	function onScroll() {
		collapsed = window.scrollY > 180;
	}

	let poll: ReturnType<typeof setInterval> | undefined;
	let tick: ReturnType<typeof setInterval> | undefined;
	onMount(() => {
		refresh();
		poll = setInterval(refresh, POLL_MS);
		tick = setInterval(() => (now = Date.now()), 1_000);
		window.addEventListener('scroll', onScroll, { passive: true });
	});
	onDestroy(() => {
		if (poll) clearInterval(poll);
		if (tick) clearInterval(tick);
		window.removeEventListener('scroll', onScroll);
	});
</script>

<svelte:head><title>Garage A · brnrd</title></svelte:head>

<main class="min-h-[130vh] bg-stone-950 px-3 pb-24 pt-3 text-stone-200 sm:px-6">
	<header
		class="garage sticky top-0 z-50 mx-auto max-w-2xl font-mono"
		class:collapsed
		data-garage="a"
	>
		{#if collapsed}
			<div class="compact" in:garageSpring>
				<span class="min-w-0 truncate text-amber-200">
					{#if current}⚡ {liveRunDisplayName(current)} {runSeconds(current, now)}{:else}─ idle{/if}
				</span>
				<span class="divider">│</span>
				<span class="shrink-0 text-sky-200"
					>▸ {next?.shell ?? '—'}·{next?.model ?? 'default'} W{compactPercent(
						nextFuel?.week ?? null
					)}</span
				>
			</div>
		{:else}
			<div class="panel" in:garageSpring>
				<div class="now-row" class:lit={Boolean(current)}>
					<strong>NOW</strong>
					{#if current}
						<span class="min-w-0 truncate"
							>⚡ {liveRunDisplayName(current)} · {runSeconds(current, now)} · {hands.length}
							{hands.length === 1 ? 'hand' : 'hands'}</span
						>
					{:else if nextWake}
						<span class="idle">─ next wake {wakeTimingText(nextWake, now)}</span>
					{:else if loading}<span class="idle">reaching the garage…</span>
					{:else}<span class="idle">─ idle</span>{/if}
				</div>

				<button
					class="next-row"
					type="button"
					aria-label="choose the runner for the next run"
					onclick={() => next && (expandedShell = next.shell ?? null)}
				>
					<strong>NEXT</strong>
					<span class="next-value">▸ {next?.shell ?? '—'} · {next?.model ?? 'default'}</span>
					<span class="fuel-inline"
						>S {compactPercent(nextFuel?.session ?? null)} · W {compactPercent(
							nextFuel?.week ?? null
						)}</span
					>
					<small>next run</small>
				</button>

				<div class="shells">
					{#each rows as row (row.shell)}
						<button
							type="button"
							class="shell-row"
							class:lit={row.inUse}
							aria-expanded={expandedShell === row.shell}
							onclick={() => (expandedShell = expandedShell === row.shell ? null : row.shell)}
						>
							<span>{expandedShell === row.shell ? '▾' : '▸'} {row.shell}</span>
							<span>S {compactPercent(row.fuel.session)} · W {compactPercent(row.fuel.week)}</span>
							<span>{row.profiles.length} cores {row.inUse ? '●' : ''}</span>
						</button>
						{#if expandedShell === row.shell}
							<div class="cores" transition:garageSpring>
								{#each row.profiles as profile (profile.name)}
									<button
										type="button"
										onclick={() => choose(profile.name)}
										disabled={profile.available === false}
										>▸ {profile.model ?? 'default'} <small>{profile.name}</small></button
									>
								{/each}
							</div>
						{/if}
					{/each}
				</div>

				<button
					type="button"
					class="project-row"
					aria-expanded={projectOpen}
					onclick={() => (projectOpen = !projectOpen)}
				>
					<span>▸ {selectedRepo?.repo_name ?? 'project'} · {environment.name}</span><span
						>▸ bench</span
					>
				</button>
				{#if projectOpen}
					<div class="project-detail" transition:garageSpring>
						<span>{selectedRepo?.repo_full_name ?? 'no connected project'}</span>
						<span
							>{environment.name}{#if environment.isDefault}<span>default</span>{/if}</span
						>
						<a href={resolve('/#bench')}>open full bench →</a>
					</div>
				{/if}
				{#if current && hands.length > 0}
					<div class="machine-lane" aria-label="hands">
						<div>⚡ {liveRunDisplayName(current)} <span>{runSeconds(current, now)}</span></div>
						{#each hands as hand (hand.run_id)}<div class="hand">
								↳ {liveRunDisplayName(hand)} <span>{runSeconds(hand, now)}</span>
							</div>{/each}
					</div>
				{/if}
				{#if note}<p class="note">{note}</p>{/if}
			</div>
		{/if}
	</header>
	<section class="mx-auto mt-8 max-w-2xl border-t border-stone-800 pt-8 text-sm text-ink-quiet">
		<p>Garage sketch A live route. Scroll to collapse the header.</p>
		<div class="h-[900px]"></div>
	</section>
</main>

<style>
	.garage {
		color: rgb(214 211 209);
	}
	.garage.collapsed {
		position: fixed;
		left: 12px;
		right: 12px;
		top: 12px;
	}
	.panel,
	.compact {
		border: 1px solid rgb(68 64 60);
		background: rgb(12 10 9 / 0.97);
		box-shadow: 0 14px 36px rgb(0 0 0 / 0.42);
	}
	.compact {
		display: flex;
		min-width: 0;
		align-items: center;
		gap: 8px;
		height: 42px;
		padding: 0 10px;
		font-size: 12px;
	}
	.divider {
		color: rgb(87 83 78);
	}
	.now-row,
	.next-row,
	.shell-row,
	.project-row {
		display: grid;
		align-items: center;
		width: 100%;
		min-width: 0;
		text-align: left;
	}
	.now-row {
		grid-template-columns: 42px minmax(0, 1fr);
		gap: 8px;
		min-height: 42px;
		padding: 8px 10px;
		border-bottom: 1px solid rgb(68 64 60);
		font-size: 12px;
	}
	.now-row.lit {
		color: rgb(253 230 138);
		box-shadow:
			inset 3px 0 rgb(245 158 11),
			inset 0 0 24px rgb(245 158 11 / 0.09);
	}
	.now-row strong,
	.next-row strong {
		font-size: 10px;
		letter-spacing: 0.12em;
		color: rgb(120 113 108);
	}
	.idle {
		color: rgb(120 113 108);
	}
	.next-row {
		grid-template-columns: 42px minmax(0, 1fr) auto;
		gap: 8px;
		min-height: 50px;
		padding: 7px 10px;
		border-bottom: 1px solid rgb(68 64 60);
	}
	.next-row:hover,
	.next-row:focus-visible {
		background: rgb(30 41 59 / 0.55);
	}
	.next-value {
		min-width: 0;
		font-size: 14px;
		color: rgb(186 230 253);
	}
	.fuel-inline {
		white-space: nowrap;
		font-size: 11px;
		color: rgb(168 162 158);
	}
	.next-row small {
		grid-column: 2 / -1;
		font-size: 9px;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: rgb(125 211 252);
	}
	.shells {
		padding: 5px 0;
	}
	.shell-row {
		grid-template-columns: minmax(72px, 1fr) auto auto;
		gap: 10px;
		min-height: 38px;
		padding: 6px 10px;
		color: rgb(168 162 158);
		font-size: 11px;
	}
	.shell-row.lit {
		color: rgb(253 230 138);
		background: rgb(245 158 11 / 0.07);
	}
	.cores {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		padding: 3px 10px 9px 25px;
	}
	.cores button {
		min-height: 34px;
		border: 1px solid rgb(68 64 60);
		padding: 4px 8px;
		color: rgb(186 230 253);
		font-size: 11px;
	}
	.cores button:disabled {
		opacity: 0.35;
	}
	.cores small {
		color: rgb(120 113 108);
	}
	.project-row {
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 8px;
		min-height: 38px;
		padding: 7px 10px;
		border-top: 1px solid rgb(68 64 60);
		font-size: 11px;
		color: rgb(168 162 158);
	}
	.project-detail {
		display: grid;
		gap: 4px;
		padding: 6px 10px 10px 25px;
		font-size: 10px;
		color: rgb(120 113 108);
	}
	.project-detail a {
		color: rgb(125 211 252);
	}
	.machine-lane {
		border-top: 1px solid rgb(41 37 36);
		padding: 7px 10px;
		font-size: 10px;
		color: rgb(168 162 158);
	}
	.machine-lane div {
		display: flex;
		justify-content: space-between;
		padding: 2px 0;
	}
	.machine-lane .hand {
		padding-left: 13px;
		color: rgb(120 113 108);
	}
	.note {
		border-top: 1px solid rgb(41 37 36);
		padding: 6px 10px;
		font-size: 10px;
		color: rgb(125 211 252);
	}
	@media (max-width: 420px) {
		.next-row {
			grid-template-columns: 42px minmax(0, 1fr);
		}
		.fuel-inline {
			grid-column: 2;
		}
		.next-row small {
			grid-column: 2;
		}
		.shell-row {
			grid-template-columns: minmax(62px, 1fr) auto;
		}
		.shell-row span:last-child {
			grid-column: 2;
			font-size: 9px;
		}
	}
</style>
