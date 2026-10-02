<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import PublishConsentNotice from '$lib/PublishConsentNotice.svelte';
	import RunNode from '$lib/RunNode.svelte';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import { fetchRunLedger, RunLedgerAuthError, type RunLedgerResponse } from '$lib/runLedger';
	import { runLedgerRowsForNode, findLiveRunForRoute } from '$lib/runNode';
	import { ReposAuthError, fetchRepos, type ConnectedRepo } from '$lib/repos';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import { LiveRunsAuthError, fetchLiveRuns, type LiveRunsResponse } from '$lib/liveRuns';
	import { startVisiblePoll } from '$lib/visiblePoll';

	const LEDGER_SPAN_MS = 7 * 24 * 60 * 60 * 1000;
	const EMPTY_SURFACE: SurfaceResponse = { generated_at: '', files: [], reported_at: null };
	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let connectedRepos = $state<ConnectedRepo[] | null>(null);
	let ledger = $state<RunLedgerResponse | null>(null);
	let ledgerError = $state<string | null>(null);
	let live = $state<LiveRunsResponse | null>(null);
	let liveError = $state<string | null>(null);
	let livePending = $state(true);
	let auth = $state({ live: false, corpus: false, ledger: false, repos: false });
	let needsLogin = $derived(Object.values(auth).some(Boolean));
	let repoSlug = $derived(page.params.repo ?? '');
	let runId = $derived(page.params.run ?? '');
	// Store account snapshots; every route selection derives both joins anew.
	let liveRun = $derived(findLiveRunForRoute(live?.runs ?? [], repoSlug, runId));
	let ledgerRows = $derived(ledger ? runLedgerRowsForNode(ledger.rows, repoSlug, runId) : null);
	let liveStale = $derived(!!liveError || !!live?.stale || !!liveRun?.daemon_stale);

	function requestFor(signal: AbortSignal): typeof fetch {
		return (input, init) =>
			fetch(input, { ...init, signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]) });
	}

	onMount(() => {
		// Each lane refreshes independently: a slow or withheld mirror cannot
		// prevent a permitted live card from arriving. All use existing APIs.
		const stops = [
			startVisiblePoll(async (signal) => {
				const request = requestFor(signal);
				try {
					const response = await fetchLiveRuns(request);
					if (signal.aborted) return;
					live = response;
					liveError = null;
					auth.live = false;
				} catch (e) {
					if (signal.aborted) return;
					if (e instanceof LiveRunsAuthError) {
						live = null;
						auth.live = true;
					}
					liveError = e instanceof Error ? e.message : 'live feed failed';
				} finally {
					if (!signal.aborted) livePending = false;
				}
			}, document),
			startVisiblePoll(async (signal) => {
				const request = requestFor(signal);
				try {
					const response = await fetchSurface(request);
					if (signal.aborted) return;
					data = response;
					error = null;
					auth.corpus = false;
				} catch (e) {
					if (signal.aborted) return;
					if (e instanceof SurfaceAuthError) {
						data = null;
						auth.corpus = true;
					}
					error = e instanceof Error ? e.message : 'corpus fetch failed';
				}
			}, document),
			startVisiblePoll(async (signal) => {
				const request = requestFor(signal);
				try {
					const response = await fetchRunLedger(
						request,
						PRODUCE_GAUGE_LEDGER_LIMIT,
						LEDGER_SPAN_MS
					);
					if (signal.aborted) return;
					ledger = response;
					ledgerError = null;
					auth.ledger = false;
				} catch (e) {
					if (signal.aborted) return;
					if (e instanceof RunLedgerAuthError) {
						ledger = null;
						auth.ledger = true;
					}
					ledgerError = e instanceof Error ? e.message : 'ledger fetch failed';
				}
			}, document),
			startVisiblePoll(
				async (signal) => {
					try {
						const response = await fetchRepos(requestFor(signal));
						if (!signal.aborted) {
							connectedRepos = response.connected_repos;
							auth.repos = false;
						}
					} catch (e) {
						if (!signal.aborted && e instanceof ReposAuthError) {
							connectedRepos = null;
							auth.repos = true;
						}
					}
				},
				document,
				60_000
			)
		];
		return () => stops.forEach((stop) => stop());
	});
</script>

<svelte:head><title>{runId} · brnrd</title></svelte:head>

<div class="mx-auto max-w-3xl px-4 pt-4 sm:px-6">
	<PublishConsentNotice repos={connectedRepos} />
	{#if needsLogin}
		<p class="panel mt-3 p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href={resolve('/login')}>Sign in</a> to refresh
			this run.
		</p>
	{/if}
	{#if liveError}
		<p class="panel mt-3 p-4 text-sm text-amber-300">
			Live feed unavailable — {liveError}.{liveRun ? ' Showing the last received card below.' : ''}
		</p>
	{:else if live?.withheld}
		<div class="panel mt-3 p-4">
			<p class="eyebrow mb-2">live feed</p>
			<WithheldNotice withheld={live.withheld} />
		</div>
	{:else if livePending}
		<p class="mt-3 font-mono text-xs text-ink-quiet">reading live status…</p>
	{/if}
	{#if error}
		<p class="panel mt-3 p-4 text-sm text-amber-300">
			Corpus unavailable — {error}.{data ? ' Showing the last received mirror below.' : ''}
		</p>
	{:else if data?.withheld}
		<div class="panel mt-3 p-4">
			<p class="eyebrow mb-2">corpus mirror</p>
			<WithheldNotice withheld={data.withheld} />
		</div>
	{:else if data === null}
		<p class="mt-3 font-mono text-xs text-ink-quiet">reading run corpus…</p>
	{/if}
</div>
<RunNode
	data={data ?? EMPTY_SURFACE}
	{repoSlug}
	{runId}
	{ledgerRows}
	ledgerStale={!!ledger?.stale || !!ledgerError}
	{ledgerError}
	ledgerWithheld={ledger?.withheld ?? null}
	{liveRun}
	{liveStale}
	corpusAvailable={data !== null && !data.withheld && !error}
/>
