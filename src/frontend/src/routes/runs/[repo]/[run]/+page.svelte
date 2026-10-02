<script lang="ts">
	// The Wyrd run node route. Both feeds are ones the dashboard already
	// publishes — the corpus mirror (`/v1/dashboard/surface`) for the node's
	// own files, the run ledger for the spend/produce receipt the mirror
	// does not carry, and the live-runs feed for identity/card while the run
	// is still burning. Neither corpus nor ledger are fetched by run id: the
	// surface is a whole snapshot, the ledger is the same windowed feed the
	// loom reads, and the live feed is account-scoped — this page filters
	// each to the one run that matches this route's repo/run slugs.
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { onMount, onDestroy, afterUpdate } from 'svelte';
	import PublishConsentNotice from '$lib/PublishConsentNotice.svelte';
	import RunNode from '$lib/RunNode.svelte';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import type { WithheldLane } from '$lib/withheld';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import { fetchRunLedger, type RunLedgerRow } from '$lib/runLedger';
	import { runLedgerRowsForNode, findLiveRunForRoute } from '$lib/runNode';
	import { ReposAuthError, fetchRepos, type ConnectedRepo } from '$lib/repos';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import { LiveRunsAuthError, fetchLiveRuns, type LiveRun, type LiveRunsResponse } from '$lib/liveRuns';

	// The widest window the ledger API honours (7 days); a run node is usually
	// opened from the loom's past shelf, whose own scrollback tops out there.
	const LEDGER_SPAN_MS = 7 * 24 * 60 * 60 * 1000;
	// Live runs poll interval: bounded, non-overlapping, stops on unmount.
	const LIVE_POLL_MS = 15000;

	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let connectedRepos = $state<ConnectedRepo[] | null>(null);
	let ledgerRows = $state<RunLedgerRow[] | null>(null);
	let ledgerWithheld = $state<WithheldLane | null>(null);
	let ledgerStale = $state(false);
	let ledgerError = $state<string | null>(null);
	
	// Live run state: current matching run, if any, and the last successful response
	let liveRun = $state<LiveRun | null>(null);
	let liveRunsResponse = $state<LiveRunsResponse | null>(null);
	let liveError = $state<string | null>(null);
	let liveStale = $state(false);
	
	// Track the last fetch promise to prevent overlap
	let fetchLivePromise: Promise<LiveRunsResponse> | null = $state(null);
	// Timer handle for cleanup on unmount
	let pollTimer: ReturnType<typeof setTimeout> | null = $state(null);
	
	let repoSlug = $derived(page.params.repo ?? '');
	let runId = $derived(page.params.run ?? '');

	// Derived: whether we have a matching live run for this route
	let hasLiveRun = $derived(liveRun !== null);
	
	// Derived: combined identity state - prefer live data when available, separate from corpus
	let liveIdentity = $derived({
		name: liveRun?.name ?? null,
		label: liveRun?.label ?? null,
		stream: liveRun?.stream ?? null,
		kind: liveRun?.kind ?? null,
		cardText: liveRun?.card_text ?? null,
		phase: liveRun?.phase ?? null,
		lifecycle: liveRun?.lifecycle ?? null,
		startedAt: liveRun?.started_at ?? null,
		lastSeen: liveRun?.last_seen ?? null
	});

	function fetchLiveRunsOnce(): Promise<LiveRunsResponse> | null {
		// Prevent overlapping fetches
		if (fetchLivePromise) return null;
		
		fetchLivePromise = fetchLiveRuns(fetch)
			.then((response) => {
				fetchLivePromise = null;
				return response;
			})
			.catch((e) => {
				fetchLivePromise = null;
				throw e;
			});
		
		return fetchLivePromise;
	}
	
	async function refreshLiveRun() {
		try {
			const promise = fetchLiveRunsOnce();
			if (!promise) return; // Overlapping fetch in progress
			
			const response = await promise;
			liveRunsResponse = response;
			liveError = null;
			liveStale = response.stale ?? false;
			
			// Match the live run for this specific route
			const matchedRun = findLiveRunForRoute(response.runs, repoSlug, runId);
			// Only update if different - prevents unnecessary re-renders
			if (matchedRun !== liveRun) {
				liveRun = matchedRun;
			}
			// If no match but we previously had one, the run may have closed - keep the last good state
			// but mark it as stale for the UI to handle
			if (!matchedRun && liveRun) {
				liveStale = true;
			}
		} catch (e) {
			if (e instanceof LiveRunsAuthError) {
				// Auth error for live runs - keep existing data if any, don't show error
				// (surface fetch already handles auth, and this is supplementary)
				liveError = null;
			} else {
				liveError = e instanceof Error ? e.message : 'live feed failed';
			}
			// Clear the promise so we can retry
			fetchLivePromise = null;
		}
	}
	
	function startPolling() {
		// Poll for live runs at regular intervals
		refreshLiveRun(); // Initial fetch
		
		pollTimer = setInterval(() => {
			refreshLiveRun();
		}, LIVE_POLL_MS);
	}
	
	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
		// Cancel any in-flight fetch by clearing the promise reference
		fetchLivePromise = null;
	}

	onMount(async () => {
		try {
			connectedRepos = (await fetchRepos()).connected_repos;
		} catch (e) {
			if (e instanceof ReposAuthError) unauthenticated = true;
		}
		try {
			data = await fetchSurface();
		} catch (e) {
			if (e instanceof SurfaceAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'run node fetch failed';
		}
		try {
			const receipts = await fetchRunLedger(fetch, PRODUCE_GAUGE_LEDGER_LIMIT, LEDGER_SPAN_MS);
			// Route segments are sanitized directory names. Match both of them:
			// one account can mirror several repos whose generated run ids may overlap.
			ledgerRows = runLedgerRowsForNode(receipts.rows, repoSlug, runId);
			ledgerWithheld = receipts.withheld ?? null;
			ledgerStale = receipts.stale;
			ledgerError = null;
		} catch (e) {
			// The receipt is a supplement, not the page. A 401 here is already
			// carried by the surface fetch (same session cookie), and any other
			// failure should leave the mirrored node readable without pretending
			// that a failed fetch proved the run was outside the ledger window.
			ledgerRows = [];
			ledgerError = e instanceof Error ? e.message : 'ledger fetch failed';
		}
		
		// Start live runs polling after initial data loads
		startPolling();
	});
	
	onDestroy(() => {
		stopPolling();
	});
	
	// React to route parameter changes without bleeding old data
	afterUpdate(() => {
		// When repoSlug or runId changes, we need to re-match the live run
		if (liveRunsResponse) {
			const matchedRun = findLiveRunForRoute(liveRunsResponse.runs, repoSlug, runId);
			if (matchedRun !== liveRun) {
				liveRun = matchedRun;
			}
			// If no match, clear the live run but keep the response for re-matching
			if (!matchedRun && liveRun) {
				liveRun = null;
				liveStale = true;
			}
		}
	});
</script>

<svelte:head><title>{runId} · brnrd</title></svelte:head>

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href={resolve('/login')}>Sign in</a> to read
			this run.
		</div>
	</div>
{:else if error}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-red-400">{error}</div>
	</div>
{:else if data === null}
	<div class="mx-auto max-w-xl p-6 font-mono text-sm text-ink-quiet">reading run node…</div>
{:else}
	<div class="mx-auto max-w-xl px-6 pt-2">
		<PublishConsentNotice repos={connectedRepos} />
	</div>
	{#if data.files.length === 0 && data.withheld}
		<div class="mx-auto max-w-xl px-6 pt-6">
			<div class="panel p-4">
				<WithheldNotice withheld={data.withheld} />
			</div>
		</div>
	{/if}
	{#if ledgerRows?.length === 0 && ledgerWithheld}
		<div class="mx-auto max-w-xl px-6 pt-6">
			<WithheldNotice withheld={ledgerWithheld} />
		</div>
	{/if}
	<!-- Render the node unless the consent marker has *replaced* it. Guarding on
	     `files.length > 0` instead loses the case this page exists for: a live
	     run whose corpus has not been mirrored yet has zero files and no
	     `withheld`, and RunNode already knows to show the card for it ("a live
	     node is not an empty one"). That combination rendered a blank page. -->
	{#if !(data.files.length === 0 && data.withheld)}
		<RunNode 
			{data} 
			{repoSlug} 
			{runId} 
			{ledgerRows} 
			{ledgerStale} 
			{ledgerError}
			liveRun={liveRun}
			liveStale={liveStale}
			liveError={liveError}
		/>
	{/if}
{/if}
