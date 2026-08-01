<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import AccountDeletion from '$lib/AccountDeletion.svelte';
	import BackchannelQueue from '$lib/BackchannelQueue.svelte';
	import BillingPanel from '$lib/BillingPanel.svelte';
	import LoomBand from '$lib/LoomBand.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import Limits from '$lib/Limits.svelte';
	import RunLedgerReceipt from '$lib/RunLedgerReceipt.svelte';
	import ProduceGauge from '$lib/ProduceGauge.svelte';
	import ControlStrip from '$lib/ControlStrip.svelte';
	import PublishConsentNotice from '$lib/PublishConsentNotice.svelte';
	import WinkWordmark from '$lib/WinkWordmark.svelte';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import type { WithheldLane } from '$lib/withheld';
	import { QuotaAuthError, fetchQuota, type QuotaShell } from '$lib/quota';
	import {
		RunnersAuthError,
		cancelWake,
		fetchRunners,
		requestWake,
		type RunnersResponse
	} from '$lib/runners';
	import {
		LiveRunsAuthError,
		ageSince,
		fetchLiveRuns,
		heartbeatLevel,
		liveRunDisplayName,
		wordmarkMood,
		type DaemonMood,
		type LiveRun
	} from '$lib/liveRuns';
	import RunNodeInline from '$lib/RunNodeInline.svelte';
	import {
		nodeDigest,
		repoRunSlug,
		runIdSlug,
		runNodeFromSurface,
		runNodeHref,
		type NodeIdentity
	} from '$lib/runNode';
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
	import { backchannelChip, backchannelCount, backchannelShowClear } from '$lib/backchannel';
	import { parseBackchannelPage } from '$lib/backchannelPage';
	import { buildWarpLayers, emberCount } from '$lib/warp';
	import WarpStack from '$lib/WarpStack.svelte';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import { LOOM_PAST_WINDOW_MS, loomPastWindowLabel } from '$lib/loomBand';
	import { LENS_ALL, applyLens } from '$lib/loomLens';
	import WorkSurface from '$lib/WorkSurface.svelte';
	import { ReposAuthError, fetchRepos, type ConnectedRepo } from '$lib/repos';
	import Landing from '$lib/Landing.svelte';
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
	let quotaWithheld = $state<WithheldLane | null>(null);
	// Three states, not two (#480's tensed-absence family): an anonymous
	// visitor must never see the dashboard scaffolding flash before the
	// landing swaps in, and a signed-in reader must never glimpse the
	// landing. 'unknown' renders neither — the boot curtain covers it.
	let authState = $state<'unknown' | 'authed' | 'anon'>('unknown');
	let now = $state(Date.now());

	let runnersData = $state<RunnersResponse | null>(null);
	let runnersWithheld = $state<WithheldLane | null>(null);
	let runnersError = $state<string | null>(null);
	// Transient receipt for the last rack action. A tap has no approval
	// step and no modal — this line is its only textual acknowledgment,
	// so a parked/canceled request is never a silent state change
	// (found live 2026-07-11: a swallowed tap read as "didn't go through").
	let runnersNote = $state<string | null>(null);
	let connectedRepos = $state<ConnectedRepo[] | null>(null);
	// Threaded into AccountDeletion's confirmation label — the same
	// `/v1/dashboard/repos` fetch that populates connectedRepos already
	// carries it, so this costs no extra round trip.
	let githubLogin = $state<string | null>(null);

	// #328 tap-to-request: optimistic-free — each action re-fetches the
	// catalog so the chip always reflects the server's row, not a guess.
	//
	// Every tap means "next wake here". Tapping the default row while a
	// request is parked restores the default (= cancels the request);
	// re-tapping the requested row is a no-op with a receipt, never a
	// silent toggle-off (which ate a live tap on 2026-07-11).
	async function tapWakeRunner(
		profileName: string,
		repoLabel: string | null,
		environment: string | null
	) {
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
			const wake = await requestWake(profileName, {
				repo_label: repoLabel,
				environment
			});
			if (runnersData) runnersData = { ...runnersData, wake_request: wake };
			runnersError = null;
			runnersNote = `next wake · ${repoLabel ?? 'default project'} · ${environment ?? 'repo policy'} · ${profileName} — tap the default runner to cancel`;
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
	let liveRunsWithheld = $state<WithheldLane | null>(null);
	let liveRunsStale = $state(false);
	let liveRunsError = $state<string | null>(null);
	// Loom slice 4 (kb/design-continuous-presence.md §3.2.1): queued intent —
	// the scheduled/queued wakes lane, narrowed to kind=scheduled; no new
	// backend data. (It reads the same account activity feed the retired
	// /activity page did — that endpoint outlives its page, since the daemon
	// still publishes to it and this lane and the live-runs view both read it.
	// Retiring the endpoint itself is a separate cut with its own blast.)
	let scheduledWakes = $state<ScheduledWake[] | null>(null);
	let activityWithheld = $state<WithheldLane | null>(null);
	let scheduledWakesError = $state<string | null>(null);
	// Loom envelope Phase 1 (kb/design-multi-workstream-concurrency.md
	// §"Loom envelope") — piggybacked on the same live-runs fetch, not a
	// separate poll; `activeSpawns` is just a derived count over the same
	// `runs` list Limits.svelte's sibling `LiveRuns` already renders.
	let spawnMaxConcurrent = $state<number | null>(null);
	let activeSpawns = $derived(liveRuns?.filter((r) => r.is_subspawn).length ?? 0);
	// #566: the daemon's own telemetry face, riding the same live-runs packet.
	// Two placements read it — the loom's NOW seam when nothing is burning, and
	// the header wordmark when no live run has a mood of its own.
	let daemonMood = $state<DaemonMood | null>(null);
	let wordmark = $derived(wordmarkMood(liveRuns, daemonMood));

	let prReviewQueue = $state<PRReviewItem[] | null>(null);
	let prReviewQueueWithheld = $state<WithheldLane | null>(null);
	let prReviewQueueStale = $state(false);
	let prReviewQueueError = $state<string | null>(null);

	let runLedgerRows = $state<RunLedgerRow[] | null>(null);
	let runLedgerWithheld = $state<WithheldLane | null>(null);
	let runLedgerStale = $state(false);
	let runLedgerError = $state<string | null>(null);
	let loomPastWindowMs = $state(LOOM_PAST_WINDOW_MS);

	let configRequests = $state<ConfigChangeRequestItem[] | null>(null);
	let configRequestsError = $state<string | null>(null);

	let surfaceData = $state<SurfaceResponse | null>(null);
	let surfaceError = $state<string | null>(null);

	// #875 v2: the backchannel's authored half lives in the same discovered
	// corpus §3 already fetches — no second endpoint, just a second reader of
	// `surfaceData`. `knownPaths` lets an item body's internal links resolve
	// the same way the corpus browser's do (`WorkSurface.svelte`).
	const BACKCHANNEL_SURFACE_PATH = 'surface/backchannel.md';
	let backchannelFile = $derived(
		surfaceData?.files.find((f) => f.path === BACKCHANNEL_SURFACE_PATH) ?? null
	);
	let authoredBackchannelItems = $derived(
		backchannelFile ? parseBackchannelPage(backchannelFile.markdown) : []
	);
	let surfaceKnownPaths = $derived(new Set((surfaceData?.files ?? []).map((f) => f.path)));
	// The §1 counter and the "does anything wait" question span three feeds,
	// not two — authored items are the primary one. The chip attributes the
	// two populations ("N authored · M derived") rather than baring the sum
	// (design-dashboard-briefing §3).
	let derivedBackchannelCount = $derived(backchannelCount(prReviewQueue, configRequests));

	// The warp (design-work-layers.md, #972 step 2): the standing intent
	// surface, discovered from `surface/layers/*.md` in the same corpus feed
	// the backchannel and corpus browser already read — no new endpoint, a
	// third reader of one fetch. Layers are authored, never derived: an
	// empty array here means nothing is strung, and the section renders as
	// one quiet line rather than not at all — the warp is a standing part of
	// the board once this ships, and absence-of-files is a fact worth a line,
	// not a hidden section (the §1 empty-queue precedent).
	let warpLayers = $derived(surfaceData ? buildWarpLayers(surfaceData.files) : []);
	let warpEmberCount = $derived(emberCount(warpLayers));
	let pendingBackchannelCount = $derived(authoredBackchannelItems.length + derivedBackchannelCount);
	// All three feeds resolved (loaded or errored) — until then the sum is a
	// partial read, and rendering it as a verdict is the measured 20 → "clear"
	// → 4 flicker. `authoredBackchannelItems.length === 0` alone cannot tell
	// "surface not yet fetched" from "no authored items"; only the feed
	// handles can.
	let backchannelFeedsResolved = $derived(
		(surfaceData !== null || surfaceError !== null) &&
			(prReviewQueue !== null || prReviewQueueError !== null) &&
			(configRequests !== null || configRequestsError !== null)
	);

	// Promote composition (2026-07-16, "A - promote: lets do it"): the loom
	// band is the page's temporal spine and the only renderer of past/now/
	// future. The old live-runs / scheduled-wakes / run-receipts *sections*
	// dissolved into this one selection-driven detail sheet: the band
	// reports a selection, the sheet answers with the full existing
	// component (LiveRuns card, receipt rows, schedule row) for just that
	// selection. No selection = the "now" default, all live runs.
	type LoomSelection = { kind: 'run' | 'wake'; id: string } | null;
	let loomSelection = $state<LoomSelection>(null);

	// The lens (wyrd §4 band 2). Page-owned for the same reason selection is:
	// the band reports a choice, the frame below answers it. This is also where
	// `/activity` and the standing §2d PR-review section went — see the lens
	// rail comment in `LoomBand.svelte`.
	let loomLens = $state<string>(LENS_ALL);

	function changeLoomLens(next: string) {
		loomLens = next;
		// A lens is a change of question, so a selection made under the old one
		// is stale. Clearing it also keeps the review lens from opening onto a
		// run node that has nothing to do with the queue it just asked for.
		loomSelection = null;
	}

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
			runLedgerWithheld = receipts.withheld ?? null;
			runLedgerStale = receipts.stale;
			runLedgerError = null;
		} catch (e) {
			if (!(e instanceof RunLedgerAuthError)) {
				runLedgerError = e instanceof Error ? e.message : 'run-ledger fetch failed';
			}
		}
	}

	// Which run this frame is about. A loom selection names one explicitly;
	// with *nothing* selected and exactly one run live, that run is the answer
	// to "what's happening now" and the frame focuses it.
	//
	// Before this, the unselected frame fell through to `<LiveRuns />`, which
	// renders neither produce nor a link to the run's own node — so the panel a
	// reader looks at by default was the one panel with no way through to the
	// node, and no manifest (maintainer, 2026-07-19: "the current run view
	// doesn't show any produce, and doesn't have a link for the detailed run
	// view"). #480 gave the node link to the *selected* sheet and never carried
	// it here. Focusing the sole live run reuses the node panel wholesale
	// rather than teaching a second component to render produce — the same
	// one-run-one-panel rule #486 settled.
	let focusRunId = $derived.by(() => {
		if (loomSelection?.kind === 'run') return loomSelection.id;
		if (loomSelection !== null) return null;
		const live = liveRuns ?? [];
		return live.length === 1 ? live[0].run_id || live[0].id : null;
	});
	let selectedLiveRuns = $derived(
		focusRunId === null
			? []
			: (liveRuns ?? []).filter((run) => (run.run_id || run.id) === focusRunId)
	);
	let selectedLedgerRows = $derived(
		focusRunId === null
			? []
			: (runLedgerRows ?? []).filter(
					(row) => (row.run_id ?? row.event_id ?? row.ended_at ?? '') === focusRunId
				)
	);
	// The node route for whatever run is selected, live or closed. A live cell
	// only ever opened this sheet, so the running run — the one a reader is
	// most likely to want — had no way through to its own node at all.
	let selectedNode = $derived.by(() => {
		if (focusRunId === null) return null;
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
	// Three states, not two. "The corpus hasn't loaded yet" and "the corpus has
	// no node for this run" used to share `false`, which made the frame fall
	// back to the LiveRuns card for the first seconds of every page load and
	// then visibly swap to the node panel when the surface fetch landed — the
	// "two bodies" flash (maintainer, 2026-07-19). Same tensed-absence family
	// as #480: while loading, the honest render is the node panel's own
	// "reading the corpus…" placeholder, not a different card that means
	// something else.
	// One digest of the selected node, shared by everything that asks the
	// corpus about it: whether a node answers at all, and (#566) the mood the
	// run's own frame recorded once it closed.
	let selectedDigest = $derived.by(() => {
		if (!selectedNode || !surfaceData) return null;
		return nodeDigest(runNodeFromSurface(surfaceData, selectedNode.repoSlug, selectedNode.runId));
	});
	let selectedNodeState = $derived.by(() => {
		if (!selectedNode) return 'none';
		if (!surfaceData) return 'loading';
		return selectedDigest?.mirrored ? 'mirrored' : 'unmirrored';
	});
	let selectedNodeAnswers = $derived(
		selectedNodeState === 'mirrored' || selectedNodeState === 'loading'
	);
	// Liveness for the node panel's dot and scan bar — the same reading the
	// LiveRuns grid makes, from the same helper, so the one panel that
	// absorbed that card keeps its language.
	let selectedLiveLevel = $derived(
		selectedLiveRuns.length > 0
			? heartbeatLevel(selectedLiveRuns[0].last_seen, now, liveRunsStale)
			: null
	);
	// The node panel wears the LiveRuns card's header grammar now (2026-07-21:
	// "best of both worlds" — the card's visual language, the panel's
	// readability). Identity facts — name, spawn chip, repo · kind, runner,
	// age, status word — travel structured instead of flattened into the
	// vitals string row, from whichever source knows the run: the live packet
	// while it burns, the ledger row after it closes.
	let selectedIdentity = $derived.by((): NodeIdentity | null => {
		const live = selectedLiveRuns[0];
		if (live) {
			const lvl = heartbeatLevel(live.last_seen, now, liveRunsStale);
			return {
				status: lvl === 'running' && live.phase ? live.phase : lvl,
				name: liveRunDisplayName(live),
				context: live.label
					? `${live.repo_label || 'unknown repo'} · ${live.kind || 'run'}`
					: live.repo_label || 'unknown repo',
				runner: [live.runner?.shell, live.runner?.core].filter(Boolean).join(' · ') || null,
				spawn: Boolean(live.is_subspawn),
				age: ageSince(live.started_at, now),
				// The live packet is the only source that carries a resolved glyph:
				// the daemon looked the handle up against `brr.emotes` before
				// publishing. An unknown handle arrives glyphless and stays that way.
				mood: live.mood ?? null,
				moodGlyph: live.mood_glyph ?? null,
				moodFrames: live.mood_frames ?? null,
				moodRest: live.mood_rest ?? null,
				moodPitch: live.mood_pitch ?? null
			};
		}
		const row = selectedLedgerRows.find((candidate) => candidate.run_id) ?? selectedLedgerRows[0];
		if (!row) return null;
		return {
			// Empty on purpose: the node's own digest speaks for a closed run's
			// status; the ledger only adds what the node doesn't know.
			status: '',
			name: row.name,
			context: row.repo_label,
			runner: [row.runner_shell, row.runner_core].filter(Boolean).join(' · ') || null,
			spawn: Boolean(row.is_subspawn),
			age: row.wall_clock_seconds ? durationLabel(row.wall_clock_seconds) : null,
			// A closed run's mood comes from its own frame — a text record, so
			// the handle survives and the glyph does not. The chip renders the
			// bare name rather than re-resolving a face the frontend can't know.
			mood: selectedDigest?.mood || null,
			// Closed run: the frame kept the handle, never the resolved face.
			moodGlyph: null,
			moodFrames: null,
			moodRest: null,
			moodPitch: null
		};
	});
	// What's left for the vitals row once identity travels structured:
	// closed-run produce counts. The live branch's elapsed/runner/phase/spawn
	// all moved into the header grammar above.
	let selectedVitals = $derived.by(() => {
		const parts: string[] = [];
		const live = selectedLiveRuns[0];
		if (!live) {
			const row = selectedLedgerRows.find((candidate) => candidate.run_id) ?? selectedLedgerRows[0];
			if (row) {
				const relics = row.external_refs ?? [];
				const prs = relics.filter((relic) => relic.kind === 'pr').length;
				const commits = relics.filter((relic) => relic.kind === 'commit').length;
				const kb = relics.filter((relic) => relic.kind === 'kb' || relic.kind === 'kb_page').length;
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

	// During a deploy cutover every `/v1` request hangs for ~30s before the
	// edge 502s, while the 2s poll keeps firing — without a guard each tick
	// stacks another refresh pass onto the same hung sockets. One pass in
	// flight at a time; the next interval tick picks up naturally.
	let refreshInFlight = false;

	async function refresh() {
		if (refreshInFlight) return;
		refreshInFlight = true;
		try {
			await refreshOnce();
		} finally {
			refreshInFlight = false;
		}
	}

	async function refreshOnce() {
		try {
			const data = await fetchQuota();
			shells = data.runner_quotas;
			quotaWithheld = data.withheld ?? null;
			authState = 'authed';
		} catch (e) {
			if (e instanceof QuotaAuthError) {
				// Anonymous: this page is the landing (#509). Stop polling —
				// six more 401s every two seconds serve nobody; signing in
				// navigates through /login and reloads the page anyway.
				authState = 'anon';
				if (pollHandle) clearInterval(pollHandle);
				return;
			}
			// A non-auth failure (backend hiccup, network) is a dashboard
			// state, not a landing one — render the dashboard with its own
			// error strings rather than a blank page.
			authState = 'authed';
		}
		try {
			const runners = await fetchRunners();
			runnersData = runners;
			runnersWithheld = runners.withheld ?? null;
			runnersError = null;
		} catch (e) {
			// 401 already surfaced by the quota fetch's unauthenticated state.
			if (!(e instanceof RunnersAuthError)) {
				runnersError = e instanceof Error ? e.message : 'runners fetch failed';
			}
		}
		if (connectedRepos === null) {
			try {
				const repos = await fetchRepos();
				connectedRepos = repos.connected_repos;
				githubLogin = repos.account.github_login;
			} catch (e) {
				if (!(e instanceof ReposAuthError)) {
					runnersError = e instanceof Error ? e.message : 'project list fetch failed';
				}
			}
		}
		try {
			const live = await fetchLiveRuns();
			liveRuns = live.runs;
			liveRunsWithheld = live.withheld ?? null;
			liveRunsStale = live.stale;
			spawnMaxConcurrent = live.spawn_max_concurrent;
			daemonMood = live.daemon_mood ?? null;
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
			activityWithheld = scheduled.withheld ?? null;
			scheduledWakesError = null;
		} catch (e) {
			if (!(e instanceof ScheduledWakesAuthError)) {
				scheduledWakesError = e instanceof Error ? e.message : 'scheduled-wakes fetch failed';
			}
		}
		try {
			const queue = await fetchPRReviewQueue();
			prReviewQueue = queue.prs;
			prReviewQueueWithheld = queue.withheld ?? null;
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

{#if authState === 'unknown'}
	<!-- The gate is still deciding (auth fetch in flight, bounded by
	     QUOTA_GATE_TIMEOUT_MS). Rendering *nothing* here was the 2026-07-21
	     black screen: a hung deploy-window request left the page in this
	     branch with an empty body. A holding line costs one element and
	     makes the wait legible; the boot curtain plays over it regardless. -->
	<main class="flex min-h-screen items-center justify-center">
		<p class="eyebrow">reaching brnrd&hellip;</p>
	</main>
{:else if authState === 'anon'}
	<!-- The landing (#509): the anonymous face of the same URL. Signed-in
	     readers never see it; anonymous ones never see the dashboard
	     scaffolding it replaces. -->
	<Landing />
{:else}
	<div class="mx-auto max-w-2xl p-6">
		<header class="ignite" style="--ignite-delay: 0ms">
			<div class="flex items-start justify-between gap-4">
				<!-- The wordmark wears the board's mood (#566): the newest live run's
				     face while something is burning, the daemon's resting one when
				     nothing is. With neither it is the plain wink the landing page
				     has always shown — the frontend owns no emote table, so "no mood
				     on the wire" renders as no mood, not as a default face. -->
				<p class="eyebrow">
					<WinkWordmark frames={wordmark.frames} pitch={wordmark.pitch} />
				</p>
				<!-- Named directly as a real gap (2026-07-08): no way to end a
			     session short of clearing cookies by hand. Small on purpose
			     ("a small one somewhere") — a plain link, not a nav bar this
			     single-page dashboard doesn't otherwise have. -->
				<div class="flex items-center gap-4">
					<!-- /activity retired 2026-07-19. Its honest content — open runs,
				     queued wakes, parked respawns — is the loom's NOW seam and
				     future shelf, and its one real affordance over them (filter
				     and scroll back through history) is the lens rail plus the
				     past-window stepper. It survived this long as a page mostly
				     because it was reporting 279 phantom running runs; once #486
				     reaped those it rendered about three rows. Folded, not
				     re-fitted. -->
					<!-- #327: repo management now lives in this same SPA at /repos,
				     backed by the /v1/dashboard/repos JSON twin. -->
					<a
						href={resolve('/repos')}
						class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
						>manage repos</a
					>
					<a
						href="/logout"
						rel="external"
						class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
						>sign out</a
					>
				</div>
			</div>
			<!-- Masthead compressed in the promote composition: the band is the
		     opening statement now, the title is a label, not a hero. -->
			<!-- "— next" cut 2026-07-22: it was staging language against a
			     dashboard that never shipped publicly, so to every real reader
			     it implied a ghost predecessor. -->
			<h1
				class="mt-1 font-mono text-lg font-semibold tracking-tight text-amber-100"
				use:typeReveal={{ text: 'resident dashboard', delay: 120 }}
			>
				resident dashboard
			</h1>
		</header>

		<PublishConsentNotice repos={connectedRepos} />

		<!-- §1 · backchannel (#875, 2026-08-01): the resident's ask queue owns
		     the fold. It began as a lens on the loom's filter rail; the
		     maintainer's live read — "still kinda bolted on … it should be one
		     of the center elements" — overruled the lens-not-section argument
		     that used to live in loomLens.ts. That argument's real concern
		     (a panel squatting the board while the answer is nothing) is
		     answered here instead: with nothing waiting, the section is one
		     quiet line. A queue is not a filter — every other chip on the rail
		     answers "which past runs am I looking at?"; this surface answers
		     "what does the resident need from me?", and a returning reader
		     asks that question first. -->
		<section
			class="ignite mt-4"
			style="--ignite-delay: 120ms"
			aria-labelledby="backchannel-heading"
		>
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">§1 · backchannel</p>
					<h2 id="backchannel-heading" class="font-mono text-sm font-semibold text-amber-100">
						what waits on you
					</h2>
				</div>
				<p class="font-mono text-[10px] text-ink-quiet">
					{backchannelChip(
						backchannelFeedsResolved,
						authoredBackchannelItems.length,
						derivedBackchannelCount
					)}
				</p>
			</div>
			<div class="mt-2">
				{#if prReviewQueueError}
					<p class="mb-2 text-sm text-red-400">{prReviewQueueError}</p>
				{/if}
				{#if configRequestsError}
					<p class="mb-2 text-sm text-red-400">{configRequestsError}</p>
				{/if}
				{#if backchannelShowClear(backchannelFeedsResolved, pendingBackchannelCount, prReviewQueueWithheld !== null)}
					<!-- The collapse that makes a standing section affordable — only
					     once every feed has answered; before that, an empty sum is
					     an unmeasured absence, not a clear queue. -->
					<p class="text-sm text-ink-quiet">nothing waits on you — the queue is clear.</p>
				{:else if pendingBackchannelCount === 0 && !backchannelFeedsResolved}
					<p class="text-sm text-ink-quiet">counting…</p>
				{:else}
					<BackchannelQueue
						authoredItems={authoredBackchannelItems}
						knownPaths={surfaceKnownPaths}
						prs={prReviewQueue ?? []}
						requests={configRequests ?? []}
						stale={prReviewQueueStale}
						{now}
						withheld={prReviewQueueWithheld}
					/>
				{/if}
			</div>
		</section>

		<!-- §1b · the warp (design-work-layers.md, taken 2026-08-01; #972):
		     the standing intent surface — account-global layers whose items
		     ripen into runs. Rendered here additively for now; the full
		     loom-page restructure (#972) makes this the future band, with
		     the backchannel above becoming its needs-you heddle. -->
		<section class="ignite mt-8" style="--ignite-delay: 140ms" aria-labelledby="warp-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">§1b · the warp</p>
					<h2 id="warp-heading" class="font-mono text-sm font-semibold text-amber-100">
						standing intent
					</h2>
				</div>
				<p class="font-mono text-[10px] text-ink-quiet">
					{surfaceData === null
						? 'stringing…'
						: warpLayers.length === 0
							? 'nothing strung'
							: `${warpLayers.length} ${warpLayers.length === 1 ? 'layer' : 'layers'} · ${warpEmberCount} ember`}
				</p>
			</div>
			<div class="mt-2">
				{#if surfaceData === null}
					<p class="text-sm text-ink-quiet">stringing…</p>
				{:else if warpLayers.length === 0}
					<p class="text-sm text-ink-quiet">
						the warp is bare — layers are authored under
						<span class="font-mono">surface/layers/</span>.
					</p>
				{:else}
					<WarpStack layers={warpLayers} knownPaths={surfaceKnownPaths} />
				{/if}
			</div>
		</section>

		<section class="ignite mt-8" style="--ignite-delay: 160ms" aria-labelledby="capacity-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">§2 · capacity + dispatch</p>
					<h2 id="capacity-heading" class="font-mono text-sm font-semibold text-amber-100">
						next wake · fuel
					</h2>
				</div>
				<p class="font-mono text-[10px] text-ink-quiet">
					{runnersError ??
						(shells === null
							? 'report loading'
							: `${shells.length} quota source${shells.length === 1 ? '' : 's'}`)}
				</p>
			</div>
			{#if runnersData?.profiles.length === 0 && runnersWithheld}
				<WithheldNotice withheld={runnersWithheld} class="mt-2 text-sm text-amber-200" />
			{/if}
			{#if shells?.length === 0 && quotaWithheld}
				<WithheldNotice withheld={quotaWithheld} class="mt-2 text-sm text-amber-200" />
			{/if}
			<ControlStrip
				runners={runnersData}
				repos={connectedRepos}
				{shells}
				{runnersError}
				{runnersNote}
				onTap={tapWakeRunner}
				ledgerRows={runLedgerRows}
				{scheduledWakes}
				{now}
			/>
		</section>

		<section class="ignite mt-8" style="--ignite-delay: 250ms" aria-labelledby="loom-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">§3 · loom</p>
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
							: 'text-ink-quiet'}"
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
					lens={loomLens}
					onLensChange={changeLoomLens}
					{daemonMood}
				/>
			</div>
			{#if scheduledWakes?.length === 0 && activityWithheld}
				<WithheldNotice withheld={activityWithheld} class="mt-2 text-sm text-amber-200" />
			{/if}
			{#if prReviewQueue?.length === 0 && prReviewQueueWithheld}
				<WithheldNotice withheld={prReviewQueueWithheld} class="mt-2 text-sm text-amber-200" />
			{/if}

			<!-- The detail sheet: the band's other half. Everything the dissolved
	     live-runs / scheduled-wakes / run-receipts sections used to say is
	     said here, for the selected thread of time only. -->
			<div class="ignite" style="--ignite-delay: 600ms">
				<div class="mt-4 flex items-baseline justify-between gap-3">
					<!-- The label names the panel that actually renders. It used to say
				     "· receipt" for any closed run, which stopped being true the
				     moment the node became the single answer. -->
					<p class="eyebrow">
						§3a · {loomSelection === null
							? focusRunId === null
								? 'now'
								: selectedNode && selectedNodeAnswers
									? 'now · node'
									: 'now'
							: loomSelection.kind === 'wake'
								? 'selected wake'
								: selectedNode && selectedNodeAnswers
									? 'selected run · node'
									: selectedLiveRuns.length > 0
										? 'selected run · live'
										: 'selected run · receipt'}
					</p>
					{#if loomSelection !== null}
						<div class="flex shrink-0 items-baseline gap-3">
							<button
								type="button"
								class="cursor-pointer font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
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
							<p class="text-sm text-ink-quiet">that wake left the schedule — it likely fired.</p>
						{/if}
					{:else if loomSelection?.kind === 'run' || focusRunId !== null}
						<!-- The loom stays the spine: a selected run fills this frame with
				     its own node instead of sending the reader to a page and
				     costing them their place in the band. One panel, not three —
				     the node speaks, with the live/receipt vitals folded into its
				     header and everything heavier behind its own expand. -->
						{#if selectedNode && selectedNodeAnswers}
							<RunNodeInline
								data={surfaceData}
								repoSlug={selectedNode.repoSlug}
								runId={selectedNode.runId}
								href={selectedNode.href}
								vitals={selectedVitals}
								liveLevel={selectedLiveLevel}
								identity={selectedIdentity}
							/>
						{:else if selectedLiveRuns.length > 0}
							<LiveRuns
								runs={selectedLiveRuns}
								stale={liveRunsStale}
								{now}
								withheld={liveRunsWithheld}
							/>
						{:else if selectedLedgerRows.length > 0}
							<RunLedgerReceipt rows={selectedLedgerRows} stale={runLedgerStale} />
						{:else}
							<p class="text-sm text-ink-quiet">
								no receipt rows for that run in the current window.
							</p>
						{/if}
					{:else if liveRunsError}
						<p class="text-sm text-red-400">{liveRunsError}</p>
					{:else if liveRuns === null}
						<p class="text-sm text-ink-quiet">Loading…</p>
					{:else}
						<!-- Multi-run "now": tapping a card *selects* it, and this same
					     sheet answers with the node panel — the identical grammar a
					     loom tap speaks. The card's old inline expansion was a third
					     rendering of the run (2026-07-20: "3 visual elements for a
					     run"); it survives only in the fallbacks above, where no
					     node can answer. -->
						<LiveRuns
							runs={liveRuns}
							stale={liveRunsStale}
							{now}
							withheld={liveRunsWithheld}
							onSelect={(id) => selectFromLoom('run', id)}
						/>
					{/if}
				</div>
			</div>

			<div class="ignite" style="--ignite-delay: 1000ms">
				<p class="eyebrow mt-6">§3b · instruments</p>
				<!-- The instruments read the loom's dial, not a constant of their own
			     (2026-07-19: "the 24h block is too static/limiting"). One time
			     scope for the section: step the past label above, and this
			     heading, the gauge caption, and its rollup all move with it.
			     The lens is the same contract in the other axis — narrow the
			     shelf to `schedule` and the gauge must count schedule runs, or
			     it becomes an instrument holding its own constant under a band
			     that has already moved, which is precisely the defect #486 fixed
			     for the time axis. -->
				{#if loomLens !== LENS_ALL}
					<p class="mt-1 font-mono text-[10px] text-ink-mute">
						lensed — counting only runs matching the selected lens
					</p>
				{/if}
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
						<p class="text-sm text-ink-quiet">Loading…</p>
					{:else if runLedgerRows.length === 0 && runLedgerWithheld}
						<WithheldNotice withheld={runLedgerWithheld} />
					{:else}
						<ProduceGauge
							rows={applyLens(runLedgerRows, loomLens)}
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
						<p class="text-sm text-ink-quiet">Loading…</p>
					{:else}
						<Limits {activeSpawns} maxSpawns={spawnMaxConcurrent} />
					{/if}
				</div>
			</div>

			<!-- §2c (the standing config-requests panel) retired 2026-07-29. The
		     page had two separate surfaces for "the resident needs you to do
		     something": PR review in the lens, settings approvals here. That is
		     one job, so it now answers as the backchannel lens in §2a. -->
		</section>

		<section class="ignite mt-10" style="--ignite-delay: 2700ms" aria-labelledby="corpus-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">§3 · corpus</p>
					<h2 id="corpus-heading" class="font-mono text-sm font-semibold text-amber-100">
						work surface
					</h2>
				</div>
				<p class="font-mono text-[10px] {surfaceError ? 'text-red-400' : 'text-ink-quiet'}">
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
					<p class="text-sm text-ink-quiet">Loading…</p>
				{:else}
					<WorkSurface data={surfaceData} />
				{/if}
			</div>
		</section>

		<section class="ignite mt-10" style="--ignite-delay: 3200ms" aria-labelledby="billing-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">§4 · account</p>
					<h2 id="billing-heading" class="font-mono text-sm font-semibold text-amber-100">
						subscription
					</h2>
				</div>
				<a
					href={resolve('/pricing')}
					class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
					>pricing</a
				>
			</div>
			<!-- The billing surface (#53's dashboard leg): the pricing page's
			     "sign in to subscribe" lands here. The panel owns its own fetches
			     (session cookie, same seam as every dashboard call) and the
			     ?billing= Checkout return notice — no polling; money state
			     changes ride the Stripe webhook, not this page's 2s cadence. -->
			<div class="mt-3">
				<BillingPanel />
			</div>
			<AccountDeletion {githubLogin} />
		</section>
	</div>
{/if}
