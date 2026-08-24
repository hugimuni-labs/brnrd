<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { garageSpring } from '$lib/transitions';
	import { environmentDisplay } from '$lib/railBench';
	import { fetchLiveRuns, liveRunDisplayName, type LiveRun } from '$lib/liveRuns';
	import { fetchQuota, quotaWindowReading, type QuotaShell } from '$lib/quota';
	import { fetchRepos, type ConnectedRepo } from '$lib/repos';
	import {
		cancelWake,
		fetchRunners,
		requestWake,
		type RunnerProfile,
		type RunnersResponse
	} from '$lib/runners';
	import { fetchScheduledWakes, type ScheduledWake } from '$lib/scheduledWakes';
	import {
		compactCore,
		garageHands,
		garageNext,
		garageNow,
		garageShells,
		nextWake
	} from '$lib/garage/garageC';

	const POLL_MS = 2_000;
	let now = $state(Date.now());
	let runners = $state<RunnersResponse | null>(null);
	let shells = $state<QuotaShell[]>([]);
	let runs = $state<LiveRun[]>([]);
	let wakes = $state<ScheduledWake[]>([]);
	let repos = $state<ConnectedRepo[]>([]);
	let drawerOpen = $state(false);
	let expandedShell = $state<string | null>(null);
	let projectOpen = $state(false);
	let repoSelection = $state<string | null>(null);
	let environmentSelection = $state<string | null>(null);
	let note = $state<string | null>(null);
	let error = $state<string | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	let current = $derived(garageNow(runs));
	let hands = $derived(current ? garageHands(runs, current) : []);
	let next = $derived(garageNext(runners, now));
	let shellRows = $derived(garageShells(runners, shells, runs, current));
	let scheduled = $derived(nextWake(wakes));
	let selectedRepo = $derived(
		repos.find(
			(repo) => repo.repo_full_name === (repoSelection ?? runners?.wake_request?.repo_label)
		) ??
			repos.find((repo) => repo.dispatch_default) ??
			repos[0]
	);
	let environment = $derived(environmentDisplay(selectedRepo, environmentSelection));

	function elapsed(run: LiveRun): string {
		if (!run.started_at) return 'live';
		const seconds = Math.max(0, Math.floor((now - Date.parse(run.started_at)) / 1000));
		if (seconds < 60) return `${seconds}s`;
		return `${Math.floor(seconds / 60)}m`;
	}

	function wakeClock(wake: ScheduledWake): string {
		if (!wake.scheduled_for) return 'pending';
		return new Date(wake.scheduled_for).toLocaleTimeString([], {
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	function fuelBlocks(shell: QuotaShell | undefined): string {
		const reading = shell?.windows[0] ? quotaWindowReading(shell.windows[0]) : null;
		if (reading?.percent === null || reading?.percent === undefined) return '▯▯▯';
		const filled = Math.max(0, Math.min(3, Math.ceil(reading.percent / 34)));
		return `${'▮'.repeat(filled)}${'▯'.repeat(3 - filled)}`;
	}

	function shellFuel(shell: QuotaShell | undefined): string {
		if (!shell || shell.windows.length === 0) return 'fuel ?';
		return shell.windows
			.slice(0, 2)
			.map((window, index) => {
				const value = quotaWindowReading(window).percent;
				return `${index === 0 ? 'S' : 'W'} ${value === null ? '?' : `${Math.round(value ?? 0)}%`}`;
			})
			.join(' ');
	}

	async function choose(profile: RunnerProfile) {
		if (!runners) return;
		try {
			if (runners.wake_request && profile.name === runners.default) {
				const wake = await cancelWake(runners.wake_request.request_id);
				runners = { ...runners, wake_request: wake.status === 'pending' ? wake : null };
				note = 'next run restored to the standing default';
			} else if (runners.wake_request?.profile === profile.name) {
				note = `${profile.name} is already the next run`;
			} else if (!runners.wake_request && profile.name === runners.default) {
				note = `${profile.name} is already the standing default`;
			} else {
				const wake = await requestWake(profile.name, {
					repo_label: selectedRepo?.repo_full_name ?? null,
					environment: environmentSelection
				});
				runners = { ...runners, wake_request: wake };
				note = `next run · ${profile.name}`;
			}
			error = null;
			expandedShell = null;
		} catch (reason) {
			error = reason instanceof Error ? reason.message : 'next-run claim failed';
		}
	}

	async function refresh() {
		const results = await Promise.allSettled([
			fetchRunners(),
			fetchQuota(),
			fetchLiveRuns(),
			fetchScheduledWakes(),
			fetchRepos()
		]);
		if (results[0].status === 'fulfilled') runners = results[0].value;
		if (results[1].status === 'fulfilled') shells = results[1].value.runner_quotas;
		if (results[2].status === 'fulfilled') runs = results[2].value.runs;
		if (results[3].status === 'fulfilled') wakes = results[3].value.rows;
		if (results[4].status === 'fulfilled') repos = results[4].value.connected_repos;
		const failure = results.find((result) => result.status === 'rejected');
		error = failure?.status === 'rejected' ? String(failure.reason) : null;
	}

	onMount(() => {
		refresh();
		timer = setInterval(() => {
			now = Date.now();
			refresh();
		}, POLL_MS);
	});
	onDestroy(() => timer && clearInterval(timer));
</script>

<svelte:head><title>Garage C · brnrd</title></svelte:head>

<main class="garage min-h-[140vh] bg-stone-950 text-stone-200">
	<header class="gauge sticky top-0 z-40" class:lit={current !== null}>
		{#key current?.run_id ?? 'idle'}
			<div class="gauge-line font-mono" in:garageSpring={{ duration: 220 }}>
				<div class="now min-w-0">
					{#if current}
						<span class="spark">⚡</span>
						<span class="truncate">{liveRunDisplayName(current)}</span>
						<span class="shrink-0"
							>{elapsed(current)}{hands.length
								? ` · ${hands.length} ${hands.length === 1 ? 'hand' : 'hands'}`
								: ''}</span
						>
					{:else if scheduled}
						<span>─ next wake {wakeClock(scheduled)}</span>
					{:else}
						<span class="text-ink-quiet">─ idle</span>
					{/if}
				</div>
				<span class="divider">│</span>
				<button
					class="next"
					type="button"
					onclick={() => {
						drawerOpen = true;
						expandedShell = next?.shell ?? null;
					}}
					aria-label="choose the next run"
				>
					<span class="next-label">next run</span>
					<span>▸ {compactCore(next)}</span>
					<span>{fuelBlocks(shells.find((shell) => shell.shell === next?.shell))}</span>
				</button>
				<span class="divider">│</span>
				<button
					class="drawer-toggle"
					type="button"
					aria-label="toggle garage drawer"
					aria-expanded={drawerOpen}
					onclick={() => (drawerOpen = !drawerOpen)}>{drawerOpen ? '▴' : '▾'}</button
				>
			</div>
		{/key}
	</header>

	{#if drawerOpen}
		<section class="drawer" aria-label="garage drawer" in:garageSpring out:garageSpring>
			{#if error}<p class="receipt error">{error}</p>{/if}
			{#if note}<p class="receipt">{note}</p>{/if}
			<div class="shells">
				{#each shellRows as row (row.shell)}
					{@const quota = shells.find((shell) => shell.shell === row.shell)}
					<div class="shell-row" class:lit={row.inUse}>
						<button
							type="button"
							class="shell-head"
							aria-expanded={expandedShell === row.shell}
							onclick={() => (expandedShell = expandedShell === row.shell ? null : row.shell)}
						>
							<strong>{row.shell}</strong><span>{row.inUse ? '●' : ''}</span><span
								>{shellFuel(quota)}</span
							><span class="grow"></span><span
								>{expandedShell === row.shell ? '▾' : '▸'} {row.profiles.length} cores</span
							>
						</button>
						{#if expandedShell === row.shell}
							<div class="cores" in:garageSpring out:garageSpring>
								{#each row.profiles as profile (profile.name)}
									<button
										type="button"
										class:chosen={next?.name === profile.name}
										onclick={() => choose(profile)}
									>
										<span>{next?.name === profile.name ? '●' : '▸'}</span>{profile.model ??
											profile.name}
									</button>
								{/each}
							</div>
						{/if}
					</div>
				{/each}
			</div>

			{#if current}
				<div class="machine-lane">
					<div><span>⚡</span> {liveRunDisplayName(current)} · {elapsed(current)}</div>
					{#each hands as hand (hand.run_id)}<div class="hand">
							↳ {liveRunDisplayName(hand)} · {elapsed(hand)}
						</div>{/each}
				</div>
			{/if}

			<div class="project-line">
				<button
					type="button"
					aria-expanded={projectOpen}
					onclick={() => (projectOpen = !projectOpen)}
				>
					<span>{selectedRepo?.repo_name ?? 'project'} · {environment.name}</span><span
						>{projectOpen ? '▾ bench' : '▸ bench'}</span
					>
				</button>
				{#if projectOpen}
					<div class="project-picks" in:garageSpring out:garageSpring>
						<p>project</p>
						{#each repos as repo (repo.id)}<button
								type="button"
								disabled={repo.daemon_status !== 'online'}
								class:chosen={selectedRepo?.id === repo.id}
								onclick={() => {
									repoSelection = repo.repo_full_name;
									environmentSelection = null;
								}}>{repo.repo_full_name}</button
							>{/each}
						<p>environment</p>
						<button
							type="button"
							class:chosen={environmentSelection === null}
							onclick={() => (environmentSelection = null)}
							>{environment.name}{#if environment.isDefault}<span class="env-badge">
									· default</span
								>{/if}</button
						>
						{#each selectedRepo?.environments ?? [] as option (option.name)}<button
								type="button"
								disabled={!option.available}
								class:chosen={environmentSelection === option.name}
								onclick={() => (environmentSelection = option.name)}>{option.name}</button
							>{/each}
					</div>
				{/if}
			</div>
		</section>
	{/if}

	<section class="demo-copy">
		<p>
			Sketch C keeps the gauge to one line forever. Scroll: the drawer stays in the document while
			the gauge stays pinned.
		</p>
	</section>
</main>

<style>
	.garage {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	.gauge {
		border-bottom: 1px solid rgb(68 64 60);
		background: rgb(12 10 9 / 0.96);
		backdrop-filter: blur(10px);
	}
	.gauge.lit {
		box-shadow: 0 0 20px rgb(245 158 11 / 0.12);
	}
	.gauge-line {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto minmax(142px, auto) auto auto;
		align-items: center;
		gap: 7px;
		height: 44px;
		padding: 0 10px;
		font-size: 11px;
		white-space: nowrap;
	}
	.now,
	.next {
		display: flex;
		min-width: 0;
		align-items: center;
		gap: 5px;
	}
	.now {
		overflow: hidden;
		color: rgb(231 229 228);
	}
	.spark,
	.lit strong {
		color: rgb(251 191 36);
	}
	.next {
		justify-content: flex-end;
		color: rgb(253 230 138);
	}
	.next-label {
		color: rgb(120 113 108);
		font-size: 8px;
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}
	.divider {
		color: rgb(68 64 60);
	}
	.drawer-toggle {
		min-width: 28px;
		min-height: 40px;
		color: rgb(214 211 209);
	}
	.drawer {
		margin: 0 auto;
		max-width: 672px;
		border: 1px solid rgb(68 64 60);
		border-top: 0;
		background: rgb(20 18 17);
		padding: 10px;
		overflow: hidden;
	}
	.receipt {
		margin: 0 0 8px;
		color: rgb(253 230 138);
		font-size: 11px;
	}
	.receipt.error {
		color: rgb(248 113 113);
	}
	.shell-row {
		border-bottom: 1px solid rgb(41 37 36);
	}
	.shell-row.lit {
		background: rgb(120 53 15 / 0.12);
	}
	.shell-head,
	.project-line > button {
		display: flex;
		width: 100%;
		min-height: 44px;
		align-items: center;
		gap: 8px;
		padding: 7px 8px;
		text-align: left;
		font-size: 12px;
	}
	.grow {
		flex: 1;
	}
	.cores {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		padding: 0 8px 9px 22px;
		overflow: hidden;
	}
	.cores button,
	.project-picks button {
		min-height: 38px;
		border: 1px solid rgb(68 64 60);
		padding: 5px 9px;
		color: rgb(168 162 158);
	}
	.cores button.chosen,
	.project-picks button.chosen {
		border-color: rgb(217 119 6);
		color: rgb(253 230 138);
	}
	.env-badge {
		color: rgb(125 211 252);
	}
	.machine-lane {
		margin-top: 9px;
		border-left: 2px solid rgb(217 119 6);
		padding: 8px 10px;
		font-size: 11px;
		color: rgb(214 211 209);
	}
	.hand {
		padding: 5px 0 0 18px;
		color: rgb(168 162 158);
	}
	.project-line {
		margin-top: 9px;
		border-top: 1px solid rgb(68 64 60);
	}
	.project-line > button {
		justify-content: space-between;
	}
	.project-picks {
		display: grid;
		gap: 5px;
		padding: 4px 8px 10px;
		overflow: hidden;
	}
	.project-picks p {
		margin: 5px 0 0;
		color: rgb(120 113 108);
		font-size: 9px;
		text-transform: uppercase;
		letter-spacing: 0.12em;
	}
	.project-picks button {
		text-align: left;
	}
	.project-picks button:disabled {
		opacity: 0.4;
	}
	.demo-copy {
		margin: 0 auto;
		max-width: 672px;
		min-height: 120vh;
		padding: 70vh 22px 40px;
		color: rgb(120 113 108);
		font-family: ui-sans-serif, system-ui;
	}
	@media (max-width: 420px) {
		.gauge-line {
			grid-template-columns: minmax(0, 1fr) auto minmax(150px, auto) auto auto;
			gap: 4px;
			padding-inline: 7px;
			font-size: 10px;
		}
		.now span:nth-child(3) {
			display: none;
		}
		.next-label {
			display: none;
		}
		.drawer {
			border-inline: 0;
		}
		.shell-head {
			font-size: 11px;
		}
	}
</style>
