<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import WindowTrack from '$lib/WindowTrack.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import PRReviewQueue from '$lib/PRReviewQueue.svelte';
	import RunLedgerReceipt from '$lib/RunLedgerReceipt.svelte';
	import ConfigRequests from '$lib/ConfigRequests.svelte';
	import { QuotaAuthError, fetchQuota, type QuotaShell } from '$lib/quota';
	import { LiveRunsAuthError, fetchLiveRuns, type LiveRun } from '$lib/liveRuns';
	import {
		PRReviewQueueAuthError,
		fetchPRReviewQueue,
		type PRReviewItem
	} from '$lib/prReviewQueue';
	import { RunLedgerAuthError, fetchRunLedger, type RunLedgerRow } from '$lib/runLedger';
	import {
		ConfigRequestsAuthError,
		fetchConfigRequests,
		type ConfigChangeRequestItem
	} from '$lib/configRequests';

	// Slice 2 (kb/design-dashboard-live-surface.md): the window-track
	// live-quota view. Polls the same daemon-published data the Jinja
	// dashboard's quota card reads (`GET /v1/dashboard/quota`), so the two
	// surfaces agree until the Jinja one is retired.
	//
	// Slice 0/1 (kb/plan-loom-realtime-build.md): 20s read like a page that
	// refreshes, not a surface you can watch tick — and the daemon-side
	// snapshots are now published on their own ~3s cadence (gates/cloud.py
	// `_dashboard_publish_loop`), so a 20s client poll was throwing away
	// freshness the backend already provides. Tightened to the "2 second
	// delay is acceptable" bar named directly.
	const POLL_MS = 2_000;
	const TICK_MS = 1_000;

	let shells = $state<QuotaShell[] | null>(null);
	let generatedAt = $state<string | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let now = $state(Date.now());

	let liveRuns = $state<LiveRun[] | null>(null);
	let liveRunsStale = $state(false);
	let liveRunsError = $state<string | null>(null);

	let prReviewQueue = $state<PRReviewItem[] | null>(null);
	let prReviewQueueStale = $state(false);
	let prReviewQueueError = $state<string | null>(null);

	let runLedgerRows = $state<RunLedgerRow[] | null>(null);
	let runLedgerStale = $state(false);
	let runLedgerError = $state<string | null>(null);

	let configRequests = $state<ConfigChangeRequestItem[] | null>(null);
	let configRequestsError = $state<string | null>(null);

	let pollHandle: ReturnType<typeof setInterval> | undefined;
	let tickHandle: ReturnType<typeof setInterval> | undefined;

	async function refresh() {
		try {
			const data = await fetchQuota();
			shells = data.runner_quotas;
			generatedAt = data.generated_at;
			error = null;
			unauthenticated = false;
		} catch (e) {
			if (e instanceof QuotaAuthError) {
				unauthenticated = true;
			} else {
				error = e instanceof Error ? e.message : 'quota fetch failed';
			}
		}
		try {
			const live = await fetchLiveRuns();
			liveRuns = live.runs;
			liveRunsStale = live.stale;
			liveRunsError = null;
		} catch (e) {
			// A 401 here is redundant with the quota fetch's own unauthenticated
			// state (same session cookie) — only surface a *different* failure.
			if (!(e instanceof LiveRunsAuthError)) {
				liveRunsError = e instanceof Error ? e.message : 'live-runs fetch failed';
			}
		}
		try {
			const queue = await fetchPRReviewQueue();
			prReviewQueue = queue.prs;
			prReviewQueueStale = queue.stale;
			prReviewQueueError = null;
		} catch (e) {
			if (!(e instanceof PRReviewQueueAuthError)) {
				prReviewQueueError = e instanceof Error ? e.message : 'pr-review-queue fetch failed';
			}
		}
		try {
			const receipts = await fetchRunLedger();
			runLedgerRows = receipts.rows;
			runLedgerStale = receipts.stale;
			runLedgerError = null;
		} catch (e) {
			if (!(e instanceof RunLedgerAuthError)) {
				runLedgerError = e instanceof Error ? e.message : 'run-ledger fetch failed';
			}
		}
		try {
			const requests = await fetchConfigRequests();
			configRequests = requests.requests;
			configRequestsError = null;
		} catch (e) {
			if (!(e instanceof ConfigRequestsAuthError)) {
				configRequestsError = e instanceof Error ? e.message : 'config-requests fetch failed';
			}
		}
	}

	onMount(() => {
		refresh();
		pollHandle = setInterval(refresh, POLL_MS);
		tickHandle = setInterval(() => {
			now = Date.now();
		}, TICK_MS);
	});

	onDestroy(() => {
		if (pollHandle) clearInterval(pollHandle);
		if (tickHandle) clearInterval(tickHandle);
	});
</script>

<div class="mx-auto max-w-2xl p-6">
	<p class="eyebrow">brnrd · resident dashboard</p>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		brnrd dashboard — next
	</h1>
	<p class="mt-2 text-sm text-stone-400">
		Live per-shell quota windows — the first real screen on the new stack. See
		<code>kb/design-dashboard-live-surface.md</code> in the main repo for the fuller live-flow plan this
		is slice 2 of.
	</p>

	<p class="eyebrow mt-6">§1 · window track</p>
	<div class="mt-2 space-y-3">
		{#if unauthenticated}
			<p class="text-sm text-stone-400">
				Sign in to see live quota windows — <a
					class="text-sky-400 underline"
					href="/login?next=/"
					rel="external">log in</a
				>.
			</p>
		{:else if error}
			<p class="text-sm text-red-400">{error}</p>
		{:else if shells === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else if shells.length === 0}
			<p class="text-sm text-stone-500">No connected daemon has reported quota yet.</p>
		{:else}
			{#each shells as shell (shell.shell)}
				<WindowTrack {shell} {now} />
			{/each}
			{#if generatedAt}
				<p class="text-right text-[11px] text-stone-600">
					daemon report as of {new Date(generatedAt).toLocaleTimeString()}
				</p>
			{/if}
		{/if}
	</div>

	<p class="eyebrow mt-8">§1b · config-change requests</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">
		pending settings requests
	</h2>
	<p class="mt-1 text-sm text-stone-400">
		Loom envelope Phase 2: an agent asking for more of a user-tunable ceiling than it currently has
		(e.g. <code>spawn.max_concurrent</code>) parks the request here rather than applying it or
		accepting a chat-typed approval — decide from the linked page.
	</p>
	<div class="mt-3">
		{#if configRequestsError}
			<p class="text-sm text-red-400">{configRequestsError}</p>
		{:else if configRequests === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<ConfigRequests requests={configRequests} {now} />
		{/if}
	</div>

	<p class="eyebrow mt-8">§2 · live runs</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">live runs</h2>
	<p class="mt-1 text-sm text-stone-400">
		What the daemon is doing right now, across every repo it touches — slice 3, the account-scoped
		view <code>coexisting-runs=unimplemented</code> named as a gap.
	</p>
	<div class="mt-3">
		{#if liveRunsError}
			<p class="text-sm text-red-400">{liveRunsError}</p>
		{:else if liveRuns === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<LiveRuns runs={liveRuns} stale={liveRunsStale} {now} />
		{/if}
	</div>

	<p class="eyebrow mt-8">§3 · pr review queue</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">PR review queue</h2>
	<div class="mt-3">
		{#if prReviewQueueError}
			<p class="text-sm text-red-400">{prReviewQueueError}</p>
		{:else if prReviewQueue === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<PRReviewQueue prs={prReviewQueue} stale={prReviewQueueStale} {now} />
		{/if}
	</div>

	<p class="eyebrow mt-8">§4 · run receipts</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">run receipts</h2>
	<div class="mt-3">
		{#if runLedgerError}
			<p class="text-sm text-red-400">{runLedgerError}</p>
		{:else if runLedgerRows === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<RunLedgerReceipt rows={runLedgerRows} stale={runLedgerStale} />
		{/if}
	</div>
</div>
