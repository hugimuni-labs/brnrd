<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import WindowTrack from '$lib/WindowTrack.svelte';
	import LoomBand from '$lib/LoomBand.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import Limits from '$lib/Limits.svelte';
	import PRReviewQueue from '$lib/PRReviewQueue.svelte';
	import RunLedgerReceipt from '$lib/RunLedgerReceipt.svelte';
	import ProduceGauge from '$lib/ProduceGauge.svelte';
	import ConfigRequests from '$lib/ConfigRequests.svelte';
	import SpoolRack from '$lib/SpoolRack.svelte';
	import { QuotaAuthError, fetchQuota, type QuotaShell } from '$lib/quota';
	import {
		RunnersAuthError,
		cancelWake,
		fetchRunners,
		requestWake,
		type RunnersResponse
	} from '$lib/runners';
	import { LiveRunsAuthError, fetchLiveRuns, type LiveRun } from '$lib/liveRuns';
	import ScheduleLane from '$lib/ScheduleLane.svelte';
	import {
		ScheduledWakesAuthError,
		fetchScheduledWakes,
		type ScheduledWake
	} from '$lib/scheduledWakes';
	import {
		PRReviewQueueAuthError,
		fetchPRReviewQueue,
		type PRReviewItem
	} from '$lib/prReviewQueue';
	import { RunLedgerAuthError, fetchRunLedger, type RunLedgerRow } from '$lib/runLedger';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import DecisionsSpace from '$lib/DecisionsSpace.svelte';
	import WorkflowPanel from '$lib/WorkflowPanel.svelte';
	import { PlansAuthError, fetchPlans, type PlansResponse } from '$lib/plans';
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

	let runnersData = $state<RunnersResponse | null>(null);
	let runnersError = $state<string | null>(null);
	// Transient receipt for the last rack action. A tap has no approval
	// step and no modal — this line is its only textual acknowledgment,
	// so a parked/canceled request is never a silent state change
	// (found live 2026-07-11: a swallowed tap read as "didn't go through").
	let runnersNote = $state<string | null>(null);

	// #328 tap-to-request: optimistic-free — each action re-fetches the
	// catalog so the chip always reflects the server's row, not a guess.
	//
	// Every tap means "next wake here". Tapping the default row while a
	// request is parked restores the default (= cancels the request);
	// re-tapping the requested row is a no-op with a receipt, never a
	// silent toggle-off (which ate a live tap on 2026-07-11).
	async function tapWakeRunner(profileName: string) {
		const parked = runnersData?.wake_request ?? null;
		if (parked && profileName === parked.profile) {
			runnersNote = `${profileName} is already requested — tap the default row to cancel`;
			return;
		}
		if (parked && profileName === runnersData?.default) {
			await cancelWakeRunner(parked.request_id);
			return;
		}
		if (!parked && profileName === runnersData?.default) {
			runnersNote = `${profileName} is the standing default — the next wake runs there anyway`;
			return;
		}
		try {
			const wake = await requestWake(profileName);
			if (runnersData) runnersData = { ...runnersData, wake_request: wake };
			runnersError = null;
			runnersNote = `next wake runs on ${profileName} — no approval needed; tap the default row to cancel`;
		} catch (e) {
			// An auth failure on a *tap* must be loud: the passive fetch may
			// stay quiet for anonymous viewers, but here the user just acted
			// and the action was dropped.
			runnersNote = null;
			runnersError =
				e instanceof RunnersAuthError
					? 'session expired — sign in again, then re-tap'
					: e instanceof Error
						? e.message
						: 'wake request failed';
		}
	}

	async function cancelWakeRunner(requestId: string) {
		try {
			const wake = await cancelWake(requestId);
			// `consumed` means the wake fired before the cancel landed —
			// show that truth briefly rather than pretending it unhappened.
			if (runnersData) {
				runnersData = {
					...runnersData,
					wake_request: wake.status === 'pending' ? wake : null
				};
			}
			runnersError = null;
			runnersNote =
				wake.status === 'consumed'
					? 'that wake already fired — the request was spent, not canceled'
					: 'wake request canceled — next wake falls back to the default';
		} catch (e) {
			runnersNote = null;
			runnersError =
				e instanceof RunnersAuthError
					? 'session expired — sign in again, then re-tap'
					: e instanceof Error
						? e.message
						: 'wake cancel failed';
		}
	}

	let liveRuns = $state<LiveRun[] | null>(null);
	let liveRunsStale = $state(false);
	let liveRunsError = $state<string | null>(null);
	// Loom slice 4 (kb/design-continuous-presence.md §3.2.1): queued intent —
	// the scheduled/queued wakes lane. Same activity feed the /activity page
	// filters, narrowed to kind=scheduled; no new backend data.
	let scheduledWakes = $state<ScheduledWake[] | null>(null);
	let scheduledWakesError = $state<string | null>(null);
	// Loom envelope Phase 1 (kb/design-multi-workstream-concurrency.md
	// §"Loom envelope") — piggybacked on the same live-runs fetch, not a
	// separate poll; `activeSpawns` is just a derived count over the same
	// `runs` list Limits.svelte's sibling `LiveRuns` already renders.
	let spawnMaxConcurrent = $state<number | null>(null);
	let activeSpawns = $derived(liveRuns?.filter((r) => r.is_subspawn).length ?? 0);

	let prReviewQueue = $state<PRReviewItem[] | null>(null);
	let prReviewQueueStale = $state(false);
	let prReviewQueueError = $state<string | null>(null);

	let runLedgerRows = $state<RunLedgerRow[] | null>(null);
	let runLedgerStale = $state(false);
	let runLedgerError = $state<string | null>(null);

	let configRequests = $state<ConfigChangeRequestItem[] | null>(null);
	let configRequestsError = $state<string | null>(null);

	let plansData = $state<PlansResponse | null>(null);
	let plansError = $state<string | null>(null);

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
			const runners = await fetchRunners();
			runnersData = runners;
			runnersError = null;
		} catch (e) {
			// 401 already surfaced by the quota fetch's unauthenticated state.
			if (!(e instanceof RunnersAuthError)) {
				runnersError = e instanceof Error ? e.message : 'runners fetch failed';
			}
		}
		try {
			const live = await fetchLiveRuns();
			liveRuns = live.runs;
			liveRunsStale = live.stale;
			spawnMaxConcurrent = live.spawn_max_concurrent;
			liveRunsError = null;
		} catch (e) {
			// A 401 here is redundant with the quota fetch's own unauthenticated
			// state (same session cookie) — only surface a *different* failure.
			if (!(e instanceof LiveRunsAuthError)) {
				liveRunsError = e instanceof Error ? e.message : 'live-runs fetch failed';
			}
		}
		try {
			const scheduled = await fetchScheduledWakes();
			scheduledWakes = scheduled.rows;
			scheduledWakesError = null;
		} catch (e) {
			if (!(e instanceof ScheduledWakesAuthError)) {
				scheduledWakesError = e instanceof Error ? e.message : 'scheduled-wakes fetch failed';
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
			const receipts = await fetchRunLedger(fetch, PRODUCE_GAUGE_LEDGER_LIMIT);
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
		try {
			const plans = await fetchPlans();
			plansData = plans;
			plansError = null;
		} catch (e) {
			if (!(e instanceof PlansAuthError)) {
				plansError = e instanceof Error ? e.message : 'plans fetch failed';
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
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · resident dashboard</p>
		<!-- Named directly as a real gap (2026-07-08): no way to end a
		     session short of clearing cookies by hand. Small on purpose
		     ("a small one somewhere") — a plain link, not a nav bar this
		     single-page dashboard doesn't otherwise have. -->
		<div class="flex items-center gap-4">
			<!-- #327: the full activity history (runs, scheduled wakes,
			     parked respawns) — a client-side route in this same SPA,
			     replacing the retired Jinja /activity page. -->
			<a
				href="/activity"
				class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
				>activity</a
			>
			<!-- #327: repo management now lives in this same SPA at /repos,
			     backed by the /v1/dashboard/repos JSON twin. -->
			<a
				href="/repos"
				class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
				>manage repos</a
			>
			<a
				href="/logout"
				rel="external"
				class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
				>sign out</a
			>
		</div>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		brnrd dashboard — next
	</h1>
	<p class="mt-2 text-sm text-stone-400">
		Live per-shell quota windows, updated as your daemons report in.
	</p>

	<p class="eyebrow mt-6">§0 · loom band</p>
	<div class="mt-2">
		<LoomBand ledgerRows={runLedgerRows} {liveRuns} {scheduledWakes} {now} />
	</div>

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

	<p class="eyebrow mt-8">§1a · spool rack</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">runners</h2>
	<p class="mt-1 text-sm text-stone-400">
		The bodies available for the next wake — every Shell+Core profile your daemons discovered
		locally, cheapest first. The marked spool is who answers unless you ask otherwise. Tap a row to
		hand the <em>next</em> wake that body — one wake, cancelable until it fires; a durable default change
		stays a conversation with the resident (a parked settings request below).
	</p>
	<div class="mt-3">
		<!-- Error above the rack, not instead of it: a failed *action* must
		     not blank the panel the user is acting on. -->
		{#if runnersError}
			<p class="mb-2 text-sm text-red-400">{runnersError}</p>
		{/if}
		{#if runnersNote}
			<p class="mb-2 font-mono text-xs text-amber-300">{runnersNote}</p>
		{/if}
		{#if runnersData === null}
			{#if !runnersError}
				<p class="text-sm text-stone-500">Loading…</p>
			{/if}
		{:else}
			<SpoolRack
				profiles={runnersData.profiles}
				defaultProfile={runnersData.default}
				stale={runnersData.stale}
				wakeRequest={runnersData.wake_request ?? null}
				onTap={tapWakeRunner}
			/>
		{/if}
	</div>

	<p class="eyebrow mt-8">§1b · config-change requests</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">
		pending settings requests
	</h2>
	<p class="mt-1 text-sm text-stone-400">
		When an agent wants to raise a limit you've set (like <code>spawn.max_concurrent</code>) past
		what it's currently allowed, it parks the request here instead of applying it — review and
		decide from the linked page.
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
		What the daemon is doing right now, across every repo it touches.
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

	<p class="eyebrow mt-8">§2a · scheduled wakes</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">scheduled wakes</h2>
	<p class="mt-1 text-sm text-stone-400">
		Queued intent — what will happen without anyone asking: self-scheduled thoughts, director ticks,
		standing upkeep. Live runs is <em>now</em>; this is <em>next</em>.
	</p>
	<div class="mt-3">
		{#if scheduledWakesError}
			<p class="text-sm text-red-400">{scheduledWakesError}</p>
		{:else if scheduledWakes === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<ScheduleLane wakes={scheduledWakes} {now} />
		{/if}
	</div>

	<p class="eyebrow mt-8">§2b · limits</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">limits</h2>
	<p class="mt-1 text-sm text-stone-400">
		Today's user-tunable ceilings, as a pressure meter — not what's happening (that's live runs
		above), what you've allowed.
	</p>
	<div class="mt-3">
		{#if liveRunsError}
			<p class="text-sm text-red-400">{liveRunsError}</p>
		{:else if liveRuns === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<Limits {activeSpawns} maxSpawns={spawnMaxConcurrent} />
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

	<p class="eyebrow mt-8">§3b · decisions space</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">decisions space</h2>
	<p class="mt-1 text-sm text-stone-400">
		The resident's own plan — ranked next moves, per repo, plus the decision ledger it keeps as it
		works. This is what drives scheduling today; read it like a co-pilot's flight plan.
	</p>
	<div class="mt-3">
		{#if plansError}
			<p class="text-sm text-red-400">{plansError}</p>
		{:else if plansData === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<DecisionsSpace data={plansData} {now} />
			{#if plansData.workflow_md}
				<div class="mt-3">
					<WorkflowPanel md={plansData.workflow_md} />
				</div>
			{/if}
		{/if}
	</div>

	<p class="eyebrow mt-8">§4 · produce gauge</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">last 24h</h2>
	<p class="mt-1 text-sm text-stone-400">
		What the last day of autonomous work spent, and what that work left behind.
	</p>
	<div class="mt-3">
		{#if runLedgerError}
			<p class="text-sm text-red-400">{runLedgerError}</p>
		{:else if runLedgerRows === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<ProduceGauge rows={runLedgerRows} stale={runLedgerStale} {now} />
		{/if}
	</div>

	<p class="eyebrow mt-8">§4a · run receipts</p>
	<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">run receipts</h2>
	<div class="mt-3">
		{#if runLedgerError}
			<p class="text-sm text-red-400">{runLedgerError}</p>
		{:else if runLedgerRows === null}
			<p class="text-sm text-stone-500">Loading…</p>
		{:else}
			<RunLedgerReceipt rows={runLedgerRows.slice(0, 10)} stale={runLedgerStale} />
		{/if}
	</div>
</div>
