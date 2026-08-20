<script lang="ts">
	import { onMount } from 'svelte';
	import { garageSpring } from '$lib/transitions';
	import { dispatcherRun, handsFor, nextProfile, shellBays } from '$lib/garage/garage';
	import { fetchLiveRuns, type LiveRun } from '$lib/liveRuns';
	import { fetchQuota, quotaWindowReading, timeUntil, type QuotaShell } from '$lib/quota';
	import {
		fetchRunners,
		requestWake,
		type RunnerProfile,
		type RunnersResponse
	} from '$lib/runners';
	import { fetchRepos, type ConnectedRepo } from '$lib/repos';
	import { environmentDisplay } from '$lib/railBench';
	import { fetchScheduledWakes, wakeTimingText, type ScheduledWake } from '$lib/scheduledWakes';

	const POLL_MS = 2_000;
	let now = $state(Date.now());
	let runners = $state<RunnersResponse | null>(null);
	let quotas = $state<QuotaShell[]>([]);
	let runs = $state<LiveRun[]>([]);
	let wakes = $state<ScheduledWake[]>([]);
	let repos = $state<ConnectedRepo[]>([]);
	let expandedShell = $state<string | null>(null);
	let projectOpen = $state(false);
	let selectedRepo = $state<ConnectedRepo | null>(null);
	let selectedEnvironment = $state<string | null>(null);
	let collapsed = $state(false);
	let note = $state<string | null>(null);

	let current = $derived(dispatcherRun(runs));
	let hands = $derived(current ? handsFor(current, runs) : []);
	let next = $derived(nextProfile(runners, now));
	let bays = $derived(shellBays(runners, quotas, runs, current));
	let sameBay = $derived(
		Boolean(
			current &&
			next &&
			current.runner.shell === next.shell &&
			(current.runner.core ?? 'default') === (next.model ?? 'default')
		)
	);
	let nextWake = $derived(
		[...wakes]
			.filter((wake) => wake.scheduled_for)
			.sort((a, b) => Date.parse(a.scheduled_for ?? '') - Date.parse(b.scheduled_for ?? ''))[0] ??
			null
	);
	let environment = $derived(environmentDisplay(selectedRepo, selectedEnvironment));

	function profileCore(profile: RunnerProfile): string {
		return profile.model ?? 'default';
	}

	function elapsed(run: LiveRun): string {
		const start = Date.parse(run.started_at ?? '');
		if (!start) return 'live';
		const seconds = Math.max(0, Math.floor((now - start) / 1000));
		return seconds < 60
			? `${seconds}s`
			: seconds < 3600
				? `${Math.floor(seconds / 60)}m`
				: `${Math.floor(seconds / 3600)}h`;
	}

	function windows(
		shell: QuotaShell | null
	): Array<{ label: string; percent: number | null; reset: string | null }> {
		return (shell?.windows ?? []).slice(0, 2).map((window) => {
			const reading = quotaWindowReading(window);
			return {
				label: window.label.toLowerCase().startsWith('week') ? 'W' : 'S',
				percent: reading.percent,
				reset: timeUntil(reading.resets_at, now)
			};
		});
	}

	async function pick(profile: RunnerProfile) {
		try {
			const wake = await requestWake(profile.name, {
				repo_label: selectedRepo?.repo_full_name ?? null,
				environment: selectedEnvironment
			});
			if (runners) runners = { ...runners, wake_request: wake };
			note = `next run → ${profile.shell ?? '?'} · ${profileCore(profile)}`;
			expandedShell = null;
		} catch (error) {
			note = error instanceof Error ? error.message : 'claim failed';
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
		if (results[1].status === 'fulfilled') quotas = results[1].value.runner_quotas;
		if (results[2].status === 'fulfilled') runs = results[2].value.runs;
		if (results[3].status === 'fulfilled') wakes = results[3].value.rows;
		if (results[4].status === 'fulfilled') {
			repos = results[4].value.connected_repos;
			selectedRepo ??= repos.find((repo) => repo.dispatch_default) ?? repos[0] ?? null;
		}
	}

	function onScroll() {
		collapsed = window.scrollY > 210;
	}

	onMount(() => {
		refresh();
		const poll = setInterval(refresh, POLL_MS);
		const tick = setInterval(() => (now = Date.now()), 1_000);
		window.addEventListener('scroll', onScroll, { passive: true });
		return () => {
			clearInterval(poll);
			clearInterval(tick);
			window.removeEventListener('scroll', onScroll);
		};
	});
</script>

<svelte:head><title>Garage · sketch B</title></svelte:head>

<main class="page">
	<header class:collapsed class="garage" aria-label="runner garage">
		{#if collapsed}
			<div class="one-line">
				<span
					>{current
						? `⚡ ${current.name} ${elapsed(current)}`
						: nextWake
							? `─ ${wakeTimingText(nextWake, now)}`
							: '─ idle'}</span
				>
				<button type="button" onclick={() => (collapsed = false)}
					>▸ {next?.shell ?? 'next'} · {next ? profileCore(next) : 'unavailable'}</button
				>
			</div>
		{:else}
			<section class="bay now" in:garageSpring>
				<div class="legend">NOW</div>
				{#if current}
					<div class="now-line">
						<strong>⚡ {current.runner.shell ?? '?'} · {current.runner.core ?? 'default'}</strong
						><span
							>{current.name} · {elapsed(current)}{hands.length
								? ` · ${hands.length} ${hands.length === 1 ? 'hand' : 'hands'}`
								: ''}</span
						>
					</div>
					{#each hands as hand (hand.run_id)}<div class="hand">
							↳ {hand.runner.shell ?? '?'} · {hand.runner.core ?? 'default'} &nbsp; {hand.name}
						</div>{/each}
					{@const fuel = windows(
						quotas.find((quota) => quota.shell === current?.runner.shell) ?? null
					)}
					<div class="fuel">
						{#each fuel as item (item.label)}<span
								>{item.label}
								{item.percent === null ? '?' : `${Math.round(item.percent)}%`}{item.reset
									? ` · ${item.reset}`
									: ''}</span
							>{/each}
					</div>
				{:else if nextWake}<div class="idle">
						─ idle · next wake {wakeTimingText(nextWake, now)}
					</div>{:else}<div class="idle">─ idle</div>{/if}
			</section>

			<section class:merged={sameBay} class="bay next">
				<div class="legend">NEXT <span>next run</span></div>
				<button
					type="button"
					class="next-button"
					onclick={() =>
						next && (expandedShell = expandedShell === next.shell ? null : (next.shell ?? null))}
				>
					▸ {next?.shell ?? 'unavailable'} · {next ? profileCore(next) : 'no core'}
					{sameBay ? '· same bay' : ''}
				</button>
			</section>

			<section class="bay bays">
				<div class="legend">BAYS</div>
				{#each bays as bay (bay.shell)}
					<div class:lit={bay.inUse} class="shell-row">
						<button
							type="button"
							class="shell-head"
							aria-expanded={expandedShell === bay.shell}
							onclick={() => (expandedShell = expandedShell === bay.shell ? null : bay.shell)}
						>
							<strong>{bay.inUse ? '⚡ ' : ''}{bay.shell}</strong>
							<span class="fuel-inline"
								>{#each windows(bay.quota) as item (item.label)}<span
										>{item.label}
										{item.percent === null ? '?' : `${Math.round(item.percent)}%`}</span
									>{/each}</span
							>
							<span>{expandedShell === bay.shell ? '▾' : '▸'} {bay.profiles.length} cores</span>
						</button>
						{#if expandedShell === bay.shell}<div class="cores" transition:garageSpring>
								{#each bay.profiles as profile (profile.name)}<button
										type="button"
										onclick={() => pick(profile)}
										>{next?.name === profile.name ? '●' : '▸'} {profileCore(profile)}</button
									>{/each}
							</div>{/if}
					</div>
				{/each}
			</section>

			<section class="bay project">
				<button type="button" class="project-head" onclick={() => (projectOpen = !projectOpen)}
					>{selectedRepo?.repo_full_name ?? 'project'} · {environment.name}<span
						>{projectOpen ? '▾' : '▸'}</span
					></button
				>
				{#if projectOpen}<div class="project-picks" transition:garageSpring>
						{#each repos as repo (repo.id)}<button
								type="button"
								onclick={() => {
									selectedRepo = repo;
									selectedEnvironment = null;
								}}>{selectedRepo?.id === repo.id ? '●' : '▸'} {repo.repo_full_name}</button
							>{/each}
						{#each selectedRepo?.environments ?? [] as option (option.name)}<button
								type="button"
								disabled={!option.available}
								onclick={() => (selectedEnvironment = option.name)}
								>{selectedEnvironment === option.name ? '●' : '▸'} {option.name}</button
							>{/each}
					</div>{/if}
			</section>
			{#if note}<p class="note">{note}</p>{/if}
		{/if}
	</header>
	<div class="road">
		<p>garage sketch b</p>
		<p>Scroll to collapse the header.</p>
	</div>
</main>

<style>
	:global(body) {
		margin: 0;
		background: #0c0a09;
		color: #d6d3d1;
	}
	.page {
		min-height: 180vh;
		background: radial-gradient(circle at 50% 0, #292524 0, #0c0a09 36rem);
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
	}
	.garage {
		position: sticky;
		top: 0;
		z-index: 30;
		width: min(100%, 48rem);
		margin: 0 auto;
		background: rgba(12, 10, 9, 0.97);
		border: 1px solid #57534e;
		box-shadow: 0 12px 36px #000;
		padding: 0 10px;
		color: #d6d3d1;
	}
	.garage.collapsed {
		height: 48px;
		padding: 0 12px;
	}
	.bay {
		position: relative;
		border-bottom: 1px solid #44403c;
		padding: 16px 8px 10px;
	}
	.legend {
		position: absolute;
		top: -1px;
		left: 8px;
		background: #0c0a09;
		padding: 0 5px;
		color: #a8a29e;
		font-size: 9px;
		letter-spacing: 0.18em;
	}
	.legend span {
		margin-left: 8px;
		color: #fbbf24;
		letter-spacing: 0.08em;
	}
	.now {
		background: linear-gradient(90deg, rgba(245, 158, 11, 0.14), transparent);
		box-shadow: inset 3px 0 #f59e0b;
	}
	.now-line {
		display: flex;
		gap: 12px;
		justify-content: space-between;
		align-items: baseline;
	}
	.now-line strong {
		color: #fef3c7;
		white-space: nowrap;
	}
	.now-line span {
		text-align: right;
		font-size: 12px;
		color: #d6d3d1;
	}
	.hand {
		margin: 7px 0 0 18px;
		color: #a8a29e;
		font-size: 11px;
	}
	.fuel,
	.fuel-inline {
		display: flex;
		gap: 14px;
		color: #a8a29e;
		font-size: 10px;
	}
	.fuel {
		margin-top: 8px;
	}
	.idle {
		font-size: 12px;
		color: #a8a29e;
	}
	.next.merged {
		margin-top: -1px;
		background: linear-gradient(90deg, rgba(245, 158, 11, 0.08), transparent);
	}
	button {
		font: inherit;
		color: inherit;
	}
	.next-button,
	.shell-head,
	.project-head {
		width: 100%;
		border: 0;
		background: transparent;
		text-align: left;
		min-height: 34px;
	}
	.next-button {
		color: #fde68a;
		font-size: 14px;
	}
	.shell-row {
		border-left: 2px solid transparent;
	}
	.shell-row.lit {
		border-left-color: #f59e0b;
		background: rgba(245, 158, 11, 0.06);
	}
	.shell-head {
		display: grid;
		grid-template-columns: minmax(80px, 1fr) auto auto;
		align-items: center;
		gap: 12px;
		padding: 5px 8px;
	}
	.shell-head > span:last-child {
		color: #78716c;
		font-size: 10px;
	}
	.cores,
	.project-picks {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		padding: 5px 8px 10px;
	}
	.cores button,
	.project-picks button {
		min-height: 38px;
		border: 1px solid #57534e;
		background: #1c1917;
		padding: 6px 10px;
		color: #d6d3d1;
	}
	.cores button:hover,
	.project-picks button:hover {
		border-color: #f59e0b;
		color: #fde68a;
	}
	.project-head {
		display: flex;
		justify-content: space-between;
		color: #a8a29e;
		font-size: 11px;
	}
	.project-picks {
		flex-direction: column;
	}
	.project-picks button {
		text-align: left;
	}
	.note {
		margin: 6px 8px;
		color: #fbbf24;
		font-size: 10px;
	}
	.one-line {
		height: 47px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		font-size: 11px;
	}
	.one-line > span {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.one-line button {
		flex: 0 0 auto;
		border: 0;
		background: transparent;
		color: #fde68a;
		white-space: nowrap;
	}
	.road {
		width: min(100%, 48rem);
		margin: 0 auto;
		padding: 70vh 12px 0;
		color: #57534e;
		text-align: center;
	}
	@media (max-width: 390px) {
		.garage {
			box-sizing: border-box;
			border-left: 0;
			border-right: 0;
		}
		.now-line {
			align-items: flex-start;
			flex-direction: column;
			gap: 3px;
		}
		.now-line span {
			text-align: left;
			max-width: 100%;
			overflow-wrap: anywhere;
		}
		.shell-head {
			grid-template-columns: minmax(64px, 1fr) auto;
		}
		.shell-head > span:last-child {
			display: none;
		}
		.fuel-inline {
			gap: 7px;
		}
		.next-button {
			white-space: normal;
			overflow-wrap: anywhere;
		}
		.one-line {
			font-size: 10px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		* {
			scroll-behavior: auto !important;
			transition: none !important;
		}
	}
</style>
