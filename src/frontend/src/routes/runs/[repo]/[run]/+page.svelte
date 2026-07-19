<script lang="ts">
	// The Wyrd run node route. Both feeds are ones the dashboard already
	// publishes — the corpus mirror (`/v1/dashboard/surface`) for the node's
	// own files, and the run ledger for the spend/produce receipt the mirror
	// does not carry. Neither is fetched by run id: the surface is a whole
	// snapshot, and the ledger is the same windowed feed the loom reads, so
	// this page adds no endpoint and no schema.
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import RunNode from '$lib/RunNode.svelte';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import { fetchRunLedger, type RunLedgerRow } from '$lib/runLedger';
	import { runLedgerRowsForNode } from '$lib/runNode';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';

	// The widest window the ledger API honours (7 days); a run node is usually
	// opened from the loom's past shelf, whose own scrollback tops out there.
	const LEDGER_SPAN_MS = 7 * 24 * 60 * 60 * 1000;

	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let ledgerRows = $state<RunLedgerRow[] | null>(null);
	let ledgerStale = $state(false);
	let ledgerError = $state<string | null>(null);

	let repoSlug = $derived(page.params.repo ?? '');
	let runId = $derived(page.params.run ?? '');

	onMount(async () => {
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
	});
</script>

<svelte:head><title>{runId} · brnrd</title></svelte:head>

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href="/login">Sign in</a> to read this run.
		</div>
	</div>
{:else if error}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-red-400">{error}</div>
	</div>
{:else if data === null}
	<div class="mx-auto max-w-xl p-6 font-mono text-sm text-ink-quiet">reading run node…</div>
{:else}
	<RunNode {data} {repoSlug} {runId} {ledgerRows} {ledgerStale} {ledgerError} />
{/if}
