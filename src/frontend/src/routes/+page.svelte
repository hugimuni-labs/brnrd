<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import LoomBand from '$lib/LoomBand.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import Limits from '$lib/Limits.svelte';
	import PRReviewQueue from '$lib/PRReviewQueue.svelte';
	import RunLedgerReceipt from '$lib/RunLedgerReceipt.svelte';
	import ProduceGauge from '$lib/ProduceGauge.svelte';
	import ConfigRequests from '$lib/ConfigRequests.svelte';
	import ControlStrip from '$lib/ControlStrip.svelte';
	import { QuotaAuthError, fetchQuota, type QuotaShell } from '$lib/quota';
	import {
		RunnersAuthError,
		cancelWake,
		fetchRunners,
		requestWake,
		type RunnersResponse
	} from '$lib/runners';
	import { LiveRunsAuthError, fetchLiveRuns, type LiveRun } from '$lib/liveRuns';
	import RunNodeInline from '$lib/RunNodeInline.svelte';
	import { nodeDigest, repoRunSlug, runIdSlug, runNodeFromSurface, runNodeHref } from '$lib/runNode';
	import { durationLabel } from '$lib/runLedger';
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
	import { LOOM_PAST_WINDOW_MS, loomPastWindowLabel } from '$lib/loomBand';
	import WorkSurface from '$lib/WorkSurface.svelte';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import { typeReveal } from '$lib/transitions';
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
	let loomPastWindowMs = $state(LOOM_PAST_WINDOW_MS);

	let configRequests = $state<ConfigChangeRequestItem[] | null>(null);
	let configRequestsError = $state<string | null>(null);

	let surfaceData = $state<SurfaceResponse | null>(null);
	let surfaceError = $state<string | null>(null);

	// Promote composition (2026-07-16, "A - promote: lets do it"): the loom
	// band is the page's temporal spine and the only renderer of past/now/
	// future. The old live-runs / scheduled-wakes / run-receipts *sections*
	// dissolved into this one selection-driven detail sheet: the band
	// reports a selection, the sheet answers with the full existing
	// component (LiveRuns card, receipt rows, schedule row) for just that
	// selection. No selection = the "now" default, all live runs.
	type LoomSelection = { kind: 'run' | 'wake'; id: string } | null;
	let loomSelection = $state<LoomSelection>(null);

	function selectFromLoom(kind: 'run' | 'wake', id: string) {
		loomSelection =
			loomSelection && loomSelection.kind === kind && loomSelection.id === id ? null : { kind, id };
	}

	function changeLoomPastWindow(windowMs: number) {
		loomPastWindowMs = windowMs;
		void refreshRunLedger();
	}

	async function refreshRunLedger() {
		try {
			// This feed also powers the 24h produce gauge. Preserve that floor
			// while letting the loom request its longer 3d/7d scrollback spans.
			const spanMs = Math.max(loomPastWindowMs, LOOM_PAST_WINDOW_MS);
			const receipts = await fetchRunLedger(fetch, PRODUCE_GAUGE_LEDGER_LIMIT, spanMs);
			runLedgerRows = receipts.rows;
			runLedgerStale = receipts.stale;
			runLedgerError = null;
		} catch (e) {
			if (!(e instanceof RunLedgerAuthError)) {
				runLedgerError = e instanceof Error ? e.message : 'run-ledger fetch failed';
			}
		}
	}

	let selectedLiveRuns = $derived(
		loomSelection?.kind === 'run'
			? (liveRuns ?? []).filter((run) => (run.run_id || run.id) === loomSelection!.id)
			: []
	);
	let selectedLedgerRows = $derived(
		loomSelection?.kind === 'run'
			? (runLedgerRows ?? []).filter(
					(row) => (row.run_id ?? row.event_id ?? row.ended_at ?? '') === loomSelection!.id
				)
			: []
	);
	// The node route for whatever run is selected, live or closed. A live cell
	// only ever opened this sheet, so the running run — the one a reader is
	// most likely to want — had no way through to its own node at all.
	let selectedNode = $derived.by(() => {
		if (loomSelection?.kind !== 'run') return null;
		const live = selectedLiveRuns[0];
		const source = live?.run_id ? live : selectedLedgerRows.find((row) => row.run_id);
		if (!source?.run_id) return null;
		return {
			repoSlug: repoRunSlug(source.repo_label),
			runId: runIdSlug(source.run_id),
			href: runNodeHref(source.repo_label, source.run_id)
		};
	});
	// One run, one panel (2026-07-19: "the live run kinda duplicates the info…
	// live run repeats after the run node block"). §2a used to stack the
	// LiveRuns card *and* the ledger receipt *and* the node — three renderings
	// of one run, from three fetches, saying the same thing in three grammars.
	//
	// The node is the answer whenever the corpus has one: it is the run's own
	// authored account of itself. What the other two carried that the node
	// doesn't — live elapsed, runner identity, produce counts — is not dropped,
	// it collapses into a single vitals line in the node's header. Only when
	// no node is mirrored (a run that closed before the weld, or one whose
	// corpus push hasn't landed) do the old cards still answer.
	let selectedNodeMirrored = $derived.by(() => {
		if (!selectedNode || !surfaceData) return false;
		const node = runNodeFromSurface(surfaceData, selectedNode.repoSlug, selectedNode.runId);
		return nodeDigest(node).mirrored;
	});
	let selectedVitals = $derived.by(() => {
		const parts: string[] = [];
		const live = selectedLiveRuns[0];
		if (live) {
			const started = live.started_at ? Date.parse(live.started_at) : Number.NaN;
			if (Number.isFinite(started)) {
				parts.push(`${durationLabel(Math.max(0, (now - started) / 1000))} elapsed`);
			}
			const runner = [live.runner?.shell, live.runner?.core].filter(Boolean).join(' · ');
			if (runner) parts.push(runner);
			if (live.phase) parts.push(live.phase);
			if (live.is_subspawn) parts.push('↳ spawn');
		} else {
			const row = selectedLedgerRows.find((candidate) => candidate.run_id) ?? selectedLedgerRows[0];
			if (row) {
				if (row.wall_clock_seconds) parts.push(durationLabel(row.wall_clock_seconds));
				const relics = row.external_refs ?? [];
				const prs = relics.filter((relic) => relic.kind === 'pr').length;
				const commits = relics.filter((relic) => relic.kind === 'commit').length;
				const kb = relics.filter(
					(relic) => relic.kind === 'kb' || relic.kind === 'kb_page'
				).length;
				const produce = [
					prs > 0 ? `${prs}pr` : '',
					commits > 0 ? `${commits}c` : '',
					kb > 0 ? `${kb}kb` : ''
				].filter(Boolean);
				if (produce.length > 0) parts.push(produce.join(' '));
			}
		}
		return parts;
	});

	let selectedWakes = $derived(
		loomSelection?.kind === 'wake'
			? (scheduledWakes ?? []).filter((wake) => wake.id === loomSelection!.id)
			: []
	);

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
		await refreshRunLedger();
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
			const surface = await fetchSurface();
			surfaceData = surface;
			surfaceError = null;
		} catch (e) {
			if (!(e instanceof SurfaceAuthError)) {
				surfaceError = e instanceof Error ? e.message : 'surface fetch failed';
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
	<header class="ignite" style="--ignite-delay: 0ms">
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
		<!-- Masthead compressed in the promote composition: the band is the
		     opening statement now, the title is a label, not a hero. -->
		<h1
			class="mt-1 font-mono text-lg font-semibold tracking-tight text-amber-100"
			use:typeReveal={{ text: 'brnrd dashboard — next', delay: 120 }}
		>
			brnrd dashboard — next
		</h1>
	</header>

	<section class="ignite mt-4" style="--ignite-delay: 160ms" aria-labelledby="capacity-heading">
		<div class="flex items-baseline justify-between gap-3">
			<div>
				<p class="eyebrow">§1 · capacity + dispatch</p>
				<h2 id="capacity-heading" class="font-mono text-sm font-semibold text-amber-100">
					next wake · fuel
				</h2>
			</div>
			<p class="font-mono text-[10px] text-stone-500">
				{runnersError ??
					(shells === null
						? 'report loading'
						: `${shells.length} quota source${shells.length === 1 ? '' : 's'}`)}
			</p>
		</div>
		<ControlStrip
			runners={runnersData}
			{shells}
			{runnersError}
			{runnersNote}
			onTap={tapWakeRunner}
		/>
	</section>

	<section class="ignite mt-8" style="--ignite-delay: 250ms" aria-labelledby="loom-heading">
		<div class="flex items-baseline justify-between gap-3">
			<div>
				<p class="eyebrow">§2 · loom</p>
				<h2 id="loom-heading" class="font-mono text-sm font-semibold text-amber-100">
					{liveRuns === null
						? 'reading the run field'
						: `${liveRuns.length} live run${liveRuns.length === 1 ? '' : 's'}`}
				</h2>
			</div>
			<p
				class="font-mono text-[10px] {liveRunsError
					? 'text-red-400'
					: liveRunsStale
						? 'text-amber-400'
						: 'text-stone-500'}"
			>
				{liveRunsError ?? (liveRunsStale ? 'stale report' : 'live')}
			</p>
		</div>
		<div class="mt-2">
			<LoomBand
				ledgerRows={runLedgerRows}
				{liveRuns}
				{scheduledWakes}
				{now}
				onSelect={selectFromLoom}
				onPastWindowChange={changeLoomPastWindow}
				selectedId={loomSelection?.id ?? null}
			/>
		</div>

		<!-- The detail sheet: the band's other half. Everything the dissolved
	     live-runs / scheduled-wakes / run-receipts sections used to say is
	     said here, for the selected thread of time only. -->
		<div class="ignite" style="--ignite-delay: 600ms">
			<div class="mt-4 flex items-baseline justify-between gap-3">
				<!-- The label names the panel that actually renders. It used to say
				     "· receipt" for any closed run, which stopped being true the
				     moment the node became the single answer. -->
				<p class="eyebrow">
					§2a · {loomSelection === null
						? 'now'
						: loomSelection.kind === 'wake'
							? 'selected wake'
							: selectedNode && selectedNodeMirrored
								? 'selected run · node'
								: selectedLiveRuns.length > 0
									? 'selected run · live'
									: 'selected run · receipt'}
				</p>
				{#if loomSelection !== null}
					<div class="flex shrink-0 items-baseline gap-3">
						<button
							type="button"
							class="cursor-pointer font-mono text-[10px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
							onclick={() => (loomSelection = null)}
						>
							✕ back to now
						</button>
					</div>
				{/if}
			</div>
			<div class="mt-2">
				{#if loomSelection?.kind === 'wake'}
					{#if scheduledWakesError}
						<p class="mb-2 text-sm text-red-400">{scheduledWakesError}</p>
					{/if}
					{#if selectedWakes.length > 0}
						<ScheduleLane wakes={selectedWakes} {now} />
					{:else}
						<p class="text-sm text-stone-500">that wake left the schedule — it likely fired.</p>
					{/if}
				{:else if loomSelection?.kind === 'run'}
					<!-- The loom stays the spine: a selected run fills this frame with
				     its own node instead of sending the reader to a page and
				     costing them their place in the band. One panel, not three —
				     the node speaks, with the live/receipt vitals folded into its
				     header and everything heavier behind its own expand. -->
					{#if selectedNode && selectedNodeMirrored}
						<RunNodeInline
							data={surfaceData}
							repoSlug={selectedNode.repoSlug}
							runId={selectedNode.runId}
							href={selectedNode.href}
							vitals={selectedVitals}
						/>
					{:else if selectedLiveRuns.length > 0}
						<LiveRuns runs={selectedLiveRuns} stale={liveRunsStale} {now} />
					{:else if selectedLedgerRows.length > 0}
						<RunLedgerReceipt rows={selectedLedgerRows} stale={runLedgerStale} />
					{:else}
						<p class="text-sm text-stone-500">
							no receipt rows for that run in the current window.
						</p>
					{/if}
				{:else if liveRunsError}
					<p class="text-sm text-red-400">{liveRunsError}</p>
				{:else if liveRuns === null}
					<p class="text-sm text-stone-500">Loading…</p>
				{:else}
					<LiveRuns runs={liveRuns} stale={liveRunsStale} {now} />
				{/if}
			</div>
		</div>

		<div class="ignite" style="--ignite-delay: 1000ms">
			<p class="eyebrow mt-6">§2b · instruments</p>
			<!-- The instruments read the loom's dial, not a constant of their own
			     (2026-07-19: "the 24h block is too static/limiting"). One time
			     scope for the section: step the past label above, and this
			     heading, the gauge caption, and its rollup all move with it. -->
			<h2
				class="font-mono text-lg font-semibold tracking-tight text-amber-100"
				use:typeReveal={{ text: `last ${loomPastWindowLabel(loomPastWindowMs)}`, delay: 1150 }}
			>
				last {loomPastWindowLabel(loomPastWindowMs)}
			</h2>
			<div class="mt-3">
				{#if runLedgerError}
					<p class="text-sm text-red-400">{runLedgerError}</p>
				{:else if runLedgerRows === null}
					<p class="text-sm text-stone-500">Loading…</p>
				{:else}
					<ProduceGauge
						rows={runLedgerRows}
						stale={runLedgerStale}
						{now}
						windowMs={loomPastWindowMs}
					/>
				{/if}
			</div>

			<!-- Full claude/codex window bars retired 2026-07-18 (maintainer ask):
		     fuel lives in the §1 capacity strip's compact bars now — one
		     surface per fact (loom-viewport §10 dedup). WindowTrack itself
		     is gone with them; its palette conventions live on in
		     statusPalette.ts and the comments that cite it. -->
			<div class="mt-4">
				{#if liveRunsError}
					<p class="text-sm text-red-400">{liveRunsError}</p>
				{:else if liveRuns === null}
					<p class="text-sm text-stone-500">Loading…</p>
				{:else}
					<Limits {activeSpawns} maxSpawns={spawnMaxConcurrent} />
				{/if}
			</div>
		</div>

		<div class="ignite" style="--ignite-delay: 1900ms">
			<p class="eyebrow mt-8">§2c · config-change requests</p>
			<h2
				class="font-mono text-lg font-semibold tracking-tight text-amber-100"
				use:typeReveal={{ text: 'pending settings requests', delay: 2050 }}
			>
				pending settings requests
			</h2>
			<div class="mt-3">
				{#if configRequestsError}
					<p class="text-sm text-red-400">{configRequestsError}</p>
				{:else if configRequests === null}
					<p class="text-sm text-stone-500">Loading…</p>
				{:else}
					<ConfigRequests requests={configRequests} {now} />
				{/if}
			</div>
		</div>

		<div class="ignite" style="--ignite-delay: 2300ms">
			<p class="eyebrow mt-8">§2d · pr review queue</p>
			<h2
				class="font-mono text-lg font-semibold tracking-tight text-amber-100"
				use:typeReveal={{ text: 'PR review queue', delay: 2450 }}
			>
				PR review queue
			</h2>
			<div class="mt-3">
				{#if prReviewQueueError}
					<p class="text-sm text-red-400">{prReviewQueueError}</p>
				{:else if prReviewQueue === null}
					<p class="text-sm text-stone-500">Loading…</p>
				{:else}
					<PRReviewQueue prs={prReviewQueue} stale={prReviewQueueStale} {now} />
				{/if}
			</div>
		</div>
	</section>

	<section class="ignite mt-10" style="--ignite-delay: 2700ms" aria-labelledby="corpus-heading">
		<div class="flex items-baseline justify-between gap-3">
			<div>
				<p class="eyebrow">§3 · corpus</p>
				<h2 id="corpus-heading" class="font-mono text-sm font-semibold text-amber-100">
					work surface
				</h2>
			</div>
			<p class="font-mono text-[10px] {surfaceError ? 'text-red-400' : 'text-stone-500'}">
				{surfaceError ??
					(surfaceData === null ? 'index loading' : `${surfaceData.files.length} pages`)}
			</p>
		</div>
		<p class="mt-1 text-sm text-stone-400">
			The shared authored corpus — discovered Markdown, not a list of pages chosen in code.
		</p>
		<div class="mt-3">
			{#if surfaceError}
				<p class="text-sm text-red-400">{surfaceError}</p>
			{:else if surfaceData === null}
				<p class="text-sm text-stone-500">Loading…</p>
			{:else}
				<WorkSurface data={surfaceData} />
			{/if}
		</div>
	</section>
</div>
