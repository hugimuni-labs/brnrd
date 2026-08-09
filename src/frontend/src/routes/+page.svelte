<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import AccountDeletion from '$lib/AccountDeletion.svelte';
	import BillingPanel from '$lib/BillingPanel.svelte';
	import PickLane from '$lib/PickLane.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import RunLedgerReceipt from '$lib/RunLedgerReceipt.svelte';
	import Cloth from '$lib/Cloth.svelte';
	import BoltSummons from '$lib/BoltSummons.svelte';
	import {
		boltsTakenStorageKey,
		readTakenBolts,
		serializeTakenBolts,
		takeAll,
		takeBolt,
		unackedBolts
	} from '$lib/bolts';
	import ControlStrip from '$lib/ControlStrip.svelte';
	import ColdStart from '$lib/ColdStart.svelte';
	import PublishConsentNotice from '$lib/PublishConsentNotice.svelte';
	import { DOCS_URL } from '$lib/publicStats';
	import WinkWordmark from '$lib/WinkWordmark.svelte';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import type { WithheldLane } from '$lib/withheld';
	import { QuotaAuthError, fetchQuota, type QuotaShell } from '$lib/quota';
	import {
		RunnersAuthError,
		cancelWake,
		fetchRunners,
		releaseSticky,
		requestWake,
		type RunnersResponse
	} from '$lib/runners';
	import {
		LiveRunsAuthError,
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
	import { ageSince, durationLabel } from '$lib/runLedger';
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
	import {
		RunLedgerAuthError,
		fetchRunLedger,
		servedWindowMs,
		type RunLedgerRow
	} from '$lib/runLedger';
	import { parseBackchannelPage } from '$lib/backchannelPage';
	import { buildWarpLayers, emberCount, restingLayers, weavingRows } from '$lib/warp';
	import ThreadLegend from '$lib/ThreadLegend.svelte';
	import { buildCrossingIndex, crossingCells, crossingThreads } from '$lib/crossing';
	import { pickRows } from '$lib/pickLane';
	import WarpBand from '$lib/WarpBand.svelte';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import { CLOTH_WINDOW_MS } from '$lib/cloth';
	import { loomPastWindowLabel } from '$lib/loomBand';
	import WorkSurface from '$lib/WorkSurface.svelte';
	import {
		ReposAuthError,
		fetchRepos,
		type Capability,
		type ConnectedRepo,
		type GitHubInstallation
	} from '$lib/repos';
	import CapabilityPanel from '$lib/CapabilityPanel.svelte';
	import Landing from '$lib/Landing.svelte';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import { glitchReveal, typeReveal } from '$lib/transitions';
	import RunBlock from '$lib/RunBlock.svelte';
	import {
		ConfigRequestsAuthError,
		fetchConfigRequests,
		type ConfigChangeRequestItem
	} from '$lib/configRequests';
	import { railScrollVerdict, scrollClockTick, type ScrollClock } from '$lib/collapse';
	import {
		machineDockTop,
		machineDockVerdict,
		machineTapVerdict,
		railDockHeight,
		type RailHeightSamples
	} from '$lib/machineDock';

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
	// GitHub App installations for this account, same fetch — ColdStart's
	// step 02 reads this to tell "app installed, still need to enable a
	// repo" apart from "nothing installed yet" (#1084).
	let installations = $state<GitHubInstallation[] | null>(null);
	// Backend-owned pairing lines for the cold start, from the same
	// `/v1/dashboard/repos` fetch — an account with no repos has no repo row
	// to read `setup_command` off, so the account-level spelling comes with
	// the list itself.
	let pairingCommand = $state<string | null>(null);
	// The capability registry (design-capability-panel.md), same
	// `/v1/dashboard/repos` fetch — additive/optional on the wire, so a
	// backend that predates #1156 leaves this `null` and the panel renders
	// nothing rather than an empty shell (`repos.ts` capabilities? comment).
	let capabilities = $state<Capability[] | null>(null);
	// Threaded into AccountDeletion's confirmation label — the same
	// `/v1/dashboard/repos` fetch that populates connectedRepos already
	// carries it, so this costs no extra round trip.
	let githubLogin = $state<string | null>(null);
	// The bolt ack store's namespace (`bolts.ts`) — same `/v1/dashboard/repos`
	// fetch's `account.id`, the id `connectPublishScopeStorageKey` already
	// keys its own localStorage entry on.
	let accountId = $state<string | null>(null);

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

	// #932's exit tap. The daemon owns the record and honours the ask on its
	// next publish tick — so the note says "releasing", and the chip clears
	// when the mirror catches up rather than pretending it already did.
	async function releaseStickyRunner() {
		try {
			await releaseSticky();
			runnersError = null;
			runnersNote = 'sticky releasing — this thread goes back to the default within a publish tick';
		} catch (e) {
			runnersNote = null;
			runnersError =
				e instanceof RunnersAuthError
					? 'session expired — sign in again, then re-tap'
					: e instanceof Error
						? e.message
						: 'sticky release failed';
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
	let runLedgerWindowMs = $state(CLOTH_WINDOW_MS);

	// The bolt's cloth-side ack store (design-the-bolt.md §The cloth side).
	// Per-viewer, client-side v1 — `bolts.ts`'s own header names the teams-era
	// successor. Loaded once `accountId` is known, persisted on every change.
	let boltsTaken = $state<string[]>([]);
	let boltsTakenLoadedFor = $state<string | null>(null);
	// Bumped by the summons strip's "view" tap to arm the lane's arrival glow.
	let boltGlowToken = $state(0);
	let unackedBoltRows = $derived(
		runLedgerRows === null ? null : unackedBolts(runLedgerRows, boltsTaken)
	);

	$effect(() => {
		if (!accountId || boltsTakenLoadedFor === accountId) return;
		try {
			boltsTaken = readTakenBolts(localStorage.getItem(boltsTakenStorageKey(accountId)));
		} catch {
			// Storage can be unavailable in a private/restricted browser — the
			// viewer just sees every bolt as unacked, never a broken page.
			boltsTaken = [];
		}
		boltsTakenLoadedFor = accountId;
	});

	function persistBoltsTaken() {
		if (!accountId || boltsTakenLoadedFor !== accountId) return;
		try {
			localStorage.setItem(boltsTakenStorageKey(accountId), serializeTakenBolts(boltsTaken));
		} catch {
			// Best-effort — the live ack state still governs this tab.
		}
	}

	function takeOneBolt(runId: string) {
		boltsTaken = takeBolt(boltsTaken, runId);
		persistBoltsTaken();
	}

	function takeAllBolts() {
		if (!unackedBoltRows || unackedBoltRows.length === 0) return;
		boltsTaken = takeAll(
			boltsTaken,
			unackedBoltRows.map((row) => row.runId)
		);
		persistBoltsTaken();
	}

	// "view" jumps to the cloth-head lane and arms its arrival glow — never a
	// force-scroll of the page's own sections, and never a modal (fork 2,
	// signed). The cloth section already carries `id="cloth-heading"`.
	function viewBolts() {
		document
			.getElementById('cloth-heading')
			?.scrollIntoView({ behavior: 'smooth', block: 'start' });
		boltGlowToken += 1;
	}

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
	// The ignition crossing, render half (#972 machine round): while an
	// ignited item's `taken:` run is live, the item rides the machine block's
	// weaving lane and the warp stack rests it — one item space, moved by
	// tense. Heat counts in the warp header stay authored (they describe the
	// file), so a header may count one more ember than the stack shows while
	// it burns.
	// The machine's fold (his 08-02 steer: the parked machine is a one-line
	// run block on top; tapping it unfolds the lane), and the one selection
	// the whole loom answers to (promote composition, 2026-07-16, amended by
	// the dissolution — the detail sheet serves every tense from this one
	// selection). The expanded verdict reads the reader's own acts only —
	// deliberately NOT `focusRunId`, which auto-focuses the sole live run:
	// keying the fold on it would auto-expand the machine whenever anything
	// burns, which is exactly the parked run block's job to prevent (his
	// steer: the block IS the pulse; the lane is one tap away).
	type LoomSelection = { kind: 'run' | 'wake'; id: string } | null;
	let loomSelection = $state<LoomSelection>(null);
	// Open on arrival (his 2026-08-03 read: "it is collapsed when I just fresh
	// load the page, and I think it should be expanded by default").
	//
	// A *constant*, deliberately, and this is the whole distinction the comment
	// above draws: keying the default on state — anything burning, a run
	// focused — makes the page's first shape depend on something the reader
	// cannot see before it paints, so the block is expanded some mornings and
	// parked others and nobody ever learns the rule. `focusRunId` stays out of
	// the verdict for exactly that reason; a constant is not that failure, it
	// is the opposite of it.
	//
	// The second half of his read — "and then collapsed when we scroll past
	// it" — needs no code and must not get any: the dock already draws the
	// short pointer form once the head sticks, with the body left open at its
	// home in the document. Making a scroll position fold `machineOpen` is THE
	// PICKER YOU CANNOT REACH (#1011), the bug he reported on the rail, and
	// `machineDock.ts` refuses it in as many words.
	let machineOpen = $state(true);
	let machineExpanded = $derived(machineOpen || loomSelection !== null);
	let liveRunIds = $derived(new Set((liveRuns ?? []).map((run) => run.run_id || run.id)));
	let weaving = $derived(weavingRows(warpLayers, liveRunIds));
	// One item space, moved by tense — but only when the machine is open to
	// receive it. Parked, the machine is a single line: a weaving item
	// resting out of the warp would render *nowhere*. So the item leaves the
	// warp stack only while the lane that carries it is actually on screen;
	// parked, it stays in the warp, lit by the legend's weaving bolt.
	let warpStackLayers = $derived(
		machineExpanded ? restingLayers(warpLayers, liveRunIds) : warpLayers
	);
	// THE CROSSING (`crossing.ts`): the warp threads in authored order, and
	// run id → the ones each run lifted, read off the `taken:` rows the weld
	// already writes. One index, three readers — the warp header's legend, the
	// pick lane's rows, the cloth's lines — so one alphabet travels the whole
	// page: same threads, same cells, same width, wherever a strip is drawn.
	// That shared vocabulary is the answer to "temporal repeating instead of
	// referencing": a run and the intent it served point at each other through
	// the strip, and neither re-lists the other. (The strips also share an x
	// *within* the lane; the cloth's rows wrap, so there they do not — the
	// alphabet is the claim, not the column.)
	// The machine's own row set. Same `pickRows` the lane draws from — one
	// computation, three readers (the rail's slim line, the parked run block,
	// the lane), so no two surfaces can disagree about which pick is burning.
	let machineRows = $derived(pickRows({ liveRuns, scheduledWakes, now }));
	let burningRows = $derived(machineRows.filter((row) => row.phase === 'picking'));
	let armedRows = $derived(machineRows.filter((row) => row.phase === 'armed'));
	// The rail's one line about the now.
	let livePick = $derived.by(() => {
		if (burningRows.length === 0) return null;
		return {
			label: burningRows[0].label,
			clock: burningRows[0].clock,
			extra: burningRows.length - 1
		};
	});
	let threads = $derived(crossingThreads(warpLayers));
	// Which layers have an item weaving right now — the answer to "which one is
	// being worked", rendered on the warp where the question gets asked rather
	// than only on the run that is doing it.
	let weavingCallSigns = $derived(new Set(weaving.map((row) => row.callSign)));
	let crossingIndex = $derived(buildCrossingIndex(warpLayers));
	// All three feeds resolved (loaded or errored) — until then the needs
	// strip's sum is a partial read, and rendering it as a verdict is the
	// measured 20 → "clear" → 4 flicker. `authoredBackchannelItems.length
	// === 0` alone cannot tell "surface not yet fetched" from "no authored
	// items"; only the feed handles can. The strip's chip is the only place
	// this state renders — the layer stack below it never hears about it.
	let backchannelFeedsResolved = $derived(
		(surfaceData !== null || surfaceError !== null) &&
			(prReviewQueue !== null || prReviewQueueError !== null) &&
			(configRequests !== null || configRequestsError !== null)
	);

	// The loom is the page (#972): the tenses replace the numbered panels.
	// The cloth owns its window constant (30d by design); the ledger fetch's
	// row limit still caps the payload, so the cloth reads its rows as
	// "latest N", not "all of 30d".
	//
	// The float is dead (his 08-02 steer: "unreliable and too flashy… against
	// good user experience"). Nothing reorders on liveness any more: the rail
	// is sticky on top, the machine sits directly under it and is almost
	// nothing while idle, and ignition *reveals* the machine in place instead
	// of moving sections around the reader.

	// The shared scroll/settle clock (2026-08-08, his steer: "the behaviour
	// of both rails is a bit buggy because they behave differently … I just
	// think it should behave more uniformly and clearly and like collapse
	// not immediately but soon after the scroll happens so that the elements
	// do not congest"). `collapse.ts` `scrollClockTick` owns the timing rule
	// — hysteresis (THE BOUNDARY THAT FLICKERED), then a settle debounce —
	// and this one effect is the one JS timer that steps *both* the rail's
	// and the dock's clock together, every tick, so they can never answer on
	// two different schedules again (#1169's actual defect).
	let railSentinel = $state<HTMLElement | null>(null);
	let railClock = $state<ScrollClock>({ settled: false, pendingAt: null });
	let dockClock = $state<ScrollClock>({ settled: false, pendingAt: null });
	let railCondensed = $derived(railClock.settled);
	// Whether the machine's one line is stuck to the top with the lane it
	// belongs to left behind at the block's home. Measured off the block's own
	// sentinel rather than inferred from `railCondensed`: the two boundaries sit
	// about sixteen pixels apart, and a travel trip (tap the docked head, land
	// at the block) can decouple them further even while the rail stays
	// condensed the whole time — it has to be the geometric fact and not a
	// neighbour's proxy. Verdict, dead band, and why travel terminates against
	// it: `machineDock.machineDockVerdict`.
	let machineDocked = $derived(dockClock.settled);
	let settleTimer: ReturnType<typeof setTimeout> | null = null;
	$effect(() => {
		const sentinel = railSentinel;
		if (!sentinel || typeof window === 'undefined') return;
		const tick = () => {
			if (settleTimer !== null) {
				clearTimeout(settleTimer);
				settleTimer = null;
			}
			const now = Date.now();
			const railTop = sentinel.getBoundingClientRect().top + window.scrollY;
			const railRaw = railScrollVerdict({
				scrollY: window.scrollY,
				railTop,
				railFullHeight,
				condensed: railClock.settled
			});
			// The sentinel's *bottom*: it carries the seam above the block as a
			// real box, so its bottom edge and the dock's in-flow top are the
			// same line. Its top is 24px higher, and that gap is the trip's
			// landing margin below — two different numbers off one element, so
			// neither is a constant nudged until it looked right. Computed
			// locally rather than read off the top-level `dockTop` derived:
			// that derived depends on `railClock` (via `railCondensed`), and
			// this effect *writes* `railClock` below — reading a derived of
			// your own write inside the same effect is a real cycle in Svelte
			// 5 (`effect_update_depth_exceeded`, driven and caught live), not
			// just a style preference. Same formula either way; `dockTop`
			// stays the template's single source of truth for the rendered
			// position.
			// Settled rail heights, never `railHeight`'s live `clientHeight`
			// binding — that live read, paired with a same-tick
			// `railCondensed` flip, is #1169's actual defect: the binding
			// updates a frame after the DOM it measures, so the dock's target
			// moved out from under its own reader for the one frame that
			// mattered. `railDockHeight` (`machineDock.ts`) reads the same
			// settled samples `dockTop` (template) does — see its own doc for
			// why an open rack outranks `condensed` here too (#1258).
			const dockRaw = machineSentinel
				? machineDockVerdict({
						home: machineSentinel.getBoundingClientRect().bottom,
						dockTop: machineDockTop(
							railDockHeight(
								{
									full: railFullHeight,
									slim: railSlimHeight,
									expanded: railExpandedHeight,
									live: railHeight
								},
								railOpen,
								railClock.settled
							),
							railClock.settled && !railOpen
						),
						docked: dockClock.settled
					})
				: false;
			const nextRail = scrollClockTick(railClock, railRaw, now);
			const nextDock = scrollClockTick(dockClock, dockRaw, now);
			// Reassign only on an actual change: `scrollClockTick` returns a
			// fresh object every call, and Svelte's `$state` dirties on
			// reference identity — reassigning an object-shaped value that is
			// only *shallowly equal* still notifies every reader, which is
			// the other half of the same cycle (this effect reads
			// `railClock.settled` above, so an unconditional reassignment
			// below reschedules the effect against itself every tick, settled
			// or not).
			if (nextRail.settled !== railClock.settled || nextRail.pendingAt !== railClock.pendingAt) {
				railClock = nextRail;
			}
			if (nextDock.settled !== dockClock.settled || nextDock.pendingAt !== dockClock.pendingAt) {
				dockClock = nextDock;
			}
			// Both clocks stepped in the one tick above — "applied to both in
			// the same frame". Reschedule against whichever settles first;
			// `tick()` re-derives everything live, so a clock that isn't due
			// yet just re-arms itself with fresh geometry next call.
			const deadlines = [nextRail.pendingAt, nextDock.pendingAt].filter(
				(deadline): deadline is number => deadline !== null
			);
			if (deadlines.length > 0) {
				settleTimer = setTimeout(tick, Math.max(0, Math.min(...deadlines) - now));
			}
		};
		tick();
		window.addEventListener('scroll', tick, { passive: true });
		window.addEventListener('resize', tick, { passive: true });
		return () => {
			window.removeEventListener('scroll', tick);
			window.removeEventListener('resize', tick);
			if (settleTimer !== null) clearTimeout(settleTimer);
		};
	});

	// The rail's flow footprint stays constant while its painted height changes
	// (2026-08-02, his "the collapsing should be more natural with the
	// scrolling"). A sticky element still occupies its own box in flow, and that
	// box sits at the very top of the document — off-screen for any reader who
	// has scrolled far enough to condense it. So when the rail shrinks by 100px,
	// the browser holds `scrollY` and every section below rises 100px under the
	// reader's eyes: the "glitch" is the page moving, not the rail.
	//
	// The spacer below the rail absorbs exactly the difference, so the document
	// height never changes with the rail's form. It is only ever non-zero while
	// the rail is condensed, and the hysteresis above guarantees condensing
	// happens only once the *whole* full rail has scrolled past — so the
	// reserved space genuinely never enters the viewport (at the old
	// single-threshold trigger it inflated while still on screen: a blank band
	// right where the rail had been). `railFullHeight` is sampled only in the
	// resting full form: an expanded rack is a panel, not the rail's own height.
	let railHeight = $state(0);
	let railFullHeight = $state(0);
	// The condensed rail's own settled height, sampled the same way
	// `railFullHeight` is (resting, not mid-transition) so `dockTop` below
	// never has to read the live `clientHeight` binding once the page has
	// condensed at least once. The `|| railHeight` fallback in `dockTop` only
	// ever fires for the very first condense on a fresh load, before this has
	// a sample to give.
	let railSlimHeight = $state(0);
	// The rack's own settled height (#1258: expanded, the panel painted over
	// the machine dock and its bottom was unreachable). `railFullHeight` and
	// `railSlimHeight` are deliberately frozen while the rack is open — see
	// their own guards — so before this existed, an open rack left `dockTop`
	// (below) parked at the *resting* rail height while the rail itself,
	// still `sticky top-0`, actually rendered up to `max-h-[100svh]` tall and
	// stayed stuck for the whole scroll range the rack occupies (z-40, over
	// the dock's z-30) — the dock was laid out correctly and painted under
	// the rail the entire time. Same discipline as the other two: sampled at
	// rest (`railHeight > 0` after the rack's content has mounted), never
	// read live inside the scroll tick (`dockRaw` below) — that live read is
	// #1169's actual defect, named where it's guarded against.
	let railExpandedHeight = $state(0);
	let machineSentinel = $state<HTMLDivElement | null>(null);
	let railOpen = $state(false);
	$effect(() => {
		if (!railCondensed && !railOpen && railHeight > 0) railFullHeight = railHeight;
	});
	$effect(() => {
		if (railCondensed && !railOpen && railHeight > 0) railSlimHeight = railHeight;
	});
	$effect(() => {
		if (railOpen && railHeight > 0) railExpandedHeight = railHeight;
	});
	let railReserve = $derived(Math.max(0, railFullHeight - railHeight));
	// The one formula both the dock's actual CSS position (template below) and
	// the tick's own raw threshold (`dockRaw` above) call — a single function
	// (`machineDock.railDockHeight`), never two call sites each deriving their
	// own version of "where the dock belongs". `condensed` still gates the 8px
	// magnet overlap (`machineDockTop`'s own docstring) at the call site, not
	// inside the height formula: that reclaim is for the slim-stacked rest
	// state, and an open rack is never that, however the page happens to be
	// scrolled.
	let railSamples = $derived<RailHeightSamples>({
		full: railFullHeight,
		slim: railSlimHeight,
		expanded: railExpandedHeight,
		live: railHeight
	});
	let dockTop = $derived(
		machineDockTop(railDockHeight(railSamples, railOpen, railCondensed), railCondensed && !railOpen)
	);

	// His proposal, verbatim: "when it's expanded, it should just somehow go to
	// the top of the page. And when it's collapsed, go back if it's possible."
	// Opening the rack while scrolled would otherwise leave a panel taller than
	// the viewport pinned at `top-0` with its own bottom unreachable — the shape
	// that made the last spool in the rack impossible to tap. Returning the
	// reader to where they were is what makes the trip cheap enough to take.
	// `$state`, matching `machineReturnY` below (2026-08-03, the rack answers
	// everywhere): neither value drives a template read, so the rune buys no
	// reactivity either one actually needs — the two were just inconsistent
	// with each other, one plain `let` and one runed, for the same shape of
	// job (remember a scroll position across a travel-and-return trip).
	let railReturnY = $state<number | null>(null);
	function onRackChange(open: boolean) {
		railOpen = open;
		if (typeof window === 'undefined') return;
		if (open) {
			if (window.scrollY > 0) {
				railReturnY = window.scrollY;
				window.scrollTo({ top: 0, behavior: 'smooth' });
			}
		} else if (railReturnY !== null) {
			const back = railReturnY;
			railReturnY = null;
			window.scrollTo({ top: back, behavior: 'smooth' });
		}
	}

	// THE DOCKED TAP (his 2026-08-03 read: "pressing on the collapsed machine
	// block doesn't really expand it, just bugs out, and stays as it was — as
	// opposed to the collapsed rack block, which expands on tap").
	//
	// The tap always worked. Only the machine's *head* is sticky, so the lane
	// it opens appears where the section actually lives in the document —
	// screens above a reader who has scrolled. Press, nothing visible, and the
	// head's `glitchReveal` redraw reads as the bug.
	//
	// Same answer the rack already gives, and deliberately not a second idiom:
	// go to the block, remember where you were, and give it back on fold (his
	// 08-02 steer for the rack, verbatim — "when it's expanded it should just
	// go to the top of the page, and when it's collapsed, go back"). Not taken:
	// docking the body as well. An expanded lane pinned to the top of a phone
	// is chrome eating the page, and it rebuilds THE PICKER YOU CANNOT REACH.
	//
	// Amended 2026-08-03 (THE DOCK THAT TAPPED WRONG, his "when the machine
	// block is scrolled up it is not collapsed, so pressing it the first time
	// doesn't expand it"): the trip above only ever ran when the tap happened
	// to be an *opening* one. Docked with the block already open, the same tap
	// was a fold — of a lane the reader could not see, at a position above
	// them, so the page below rose by exactly one lane's height ("the menu hits
	// scrolled randomly a bit"). Docked, the head is a pointer and every tap on
	// it travels; only a tap taken with the lane on screen may fold. The
	// verdict is `machineTapVerdict`, and the whole argument lives with it.
	let machineReturnY = $state<number | null>(null);
	function onMachineToggle() {
		const tap = machineTapVerdict(machineExpanded, machineDocked);
		if (tap.open === true) {
			machineOpen = true;
		} else if (tap.open === false) {
			machineOpen = false;
			loomSelection = null;
		}
		if (typeof window === 'undefined') return;
		if (tap.travel) {
			if (!machineSentinel) return;
			// The sentinel, never the dock itself. A stuck `sticky` element
			// reports its *stuck* viewport position, so measuring it gives
			// back the offset it is already at — the first build did exactly
			// that and scrolled 1400 -> 1392, an 8px shrug. A zero-height
			// sibling in normal flow is the only thing on the page that
			// still knows where the block lives. Same trick `railSentinel`
			// already plays one section above.
			machineReturnY = window.scrollY;
			const home = window.scrollY + machineSentinel.getBoundingClientRect().top;
			window.scrollTo({ top: Math.max(0, home - railHeight), behavior: 'smooth' });
		} else if (tap.open === false && machineReturnY !== null) {
			const back = machineReturnY;
			machineReturnY = null;
			window.scrollTo({ top: back, behavior: 'smooth' });
		}
	}

	// The library open ask (the warp's "page →", 08-02): the token
	// distinguishes repeat asks for one path from a stale request riding a
	// re-render; the corpus browser answers, this page scrolls to it.
	let libraryRequest = $state<{ path: string; token: number }>({ path: '', token: 0 });
	function openInLibrary(path: string) {
		libraryRequest = { path, token: libraryRequest.token + 1 };
		document.getElementById('corpus-heading')?.scrollIntoView({ behavior: 'smooth' });
	}

	// Promote composition (2026-07-16, "A - promote: lets do it"), amended
	// by the dissolution (2026-08-02): the page's tenses are the spine now —
	// cloth (past), band (now), rack's future shelf (future) — and this one
	// selection-driven detail sheet still answers for all of them: the band
	// or the shelf reports a selection, the sheet answers with the full
	// existing component (node panel, LiveRuns card, receipt rows, schedule
	// row) for just that selection. No selection = the "now" default, all
	// live runs.
	// (`loomSelection` itself is declared above the machine derivations it
	// participates in.)

	// The lens (wyrd §4 band 2) lived here as page state while the band wore
	// the chips. The dissolution moved the rail into the cloth — the chips
	// lens the past inventory, and the cloth is the past's one object — and
	// the lens became the cloth's own view state, like a fold: nothing
	// outside the cloth ever answered to it once the shelf was gone.

	function selectFromLoom(kind: 'run' | 'wake', id: string) {
		loomSelection =
			loomSelection && loomSelection.kind === kind && loomSelection.id === id ? null : { kind, id };
	}

	async function refreshRunLedger() {
		try {
			// One feed, two readers: the shed's receipt fallback and the cloth
			// (the band's past shelf dissolved into the latter). The span is the
			// cloth's 30d window; the row limit still bounds the payload on the
			// 2s poll, so the cloth reads its rows as "latest N", not "all of
			// 30d".
			const receipts = await fetchRunLedger(fetch, PRODUCE_GAUGE_LEDGER_LIMIT, CLOTH_WINDOW_MS);
			runLedgerRows = receipts.rows;
			runLedgerWithheld = receipts.withheld ?? null;
			runLedgerStale = receipts.stale;
			runLedgerWindowMs = servedWindowMs(receipts.span_seconds_served, CLOTH_WINDOW_MS);
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

	// The cold-start block's own cadence — see refreshOnce(). `0` means the
	// list has never landed, so the first pass is never throttled.
	const COLD_REPO_POLL_MS = 15_000;
	let coldRepoCheckAt = 0;
	function coldRefetchDue(): boolean {
		return Date.now() - coldRepoCheckAt >= COLD_REPO_POLL_MS;
	}

	// Mirrors ColdStart.svelte's own `daemonEverPaired` — the block (and so
	// this poll) has to keep watching past "a repo exists" and up to "a
	// daemon actually registered" (#1084): the old `repos.length === 0` gate
	// stopped polling the instant a repo was enabled, which is exactly the
	// state that used to hide the pairing step for good. `never_started`
	// (#1243) belongs in this set for the same reason `offline` does — a
	// crash-looping daemon is still a daemon that registered.
	function daemonNotYetPaired(repos: ConnectedRepo[] | null): boolean {
		return (
			repos === null ||
			!repos.some(
				(r) =>
					r.daemon_status === 'online' ||
					r.daemon_status === 'offline' ||
					r.daemon_status === 'never_started'
			)
		);
	}

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
		// Once, normally — the repo list is not a live surface. The one
		// exception is a cold account: while the cold-start block is still
		// showing (no repo, or a repo with no daemon ever paired) the page
		// has to notice the moment that changes (in the other tab this very
		// block sends the reader to). A first-run panel that outstays its
		// state is a worse bug than the blank page it replaced.
		//
		// On its own clock, though. This loop runs at POLL_MS = 2s, and the
		// state it is watching for changes at human speed: the reader has to
		// reach another page, install a GitHub App, and come back. Riding the
		// 2s tick would spend thirty GETs a minute, for as long as a tab
		// stays open, on precisely the accounts that have nothing — and the
		// answer arrives no sooner. So: a slow interval, plus a refetch the
		// moment the tab regains focus, which is the *actual* signal that the
		// reader has come back from doing the thing.
		if (connectedRepos === null || (daemonNotYetPaired(connectedRepos) && coldRefetchDue())) {
			try {
				coldRepoCheckAt = Date.now();
				const repos = await fetchRepos();
				connectedRepos = repos.connected_repos;
				installations = repos.installations;
				pairingCommand = repos.pairing_command ?? null;
				githubLogin = repos.account.github_login;
				accountId = repos.account.id;
				capabilities = repos.capabilities ?? null;
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

	// Coming back to this tab is the one honest event that says "I may have
	// just enabled a repo". Cheaper than any interval and strictly faster
	// than all of them, so the throttle above never costs the reader a wait
	// they can perceive.
	function onFocus() {
		if (connectedRepos !== null && daemonNotYetPaired(connectedRepos)) {
			coldRepoCheckAt = 0;
			refresh();
		}
	}

	onMount(() => {
		refresh();
		pollHandle = setInterval(refresh, POLL_MS);
		tickHandle = setInterval(() => {
			now = Date.now();
		}, TICK_MS);
		window.addEventListener('focus', onFocus);
	});

	onDestroy(() => {
		if (pollHandle) clearInterval(pollHandle);
		if (tickHandle) clearInterval(tickHandle);
		window.removeEventListener('focus', onFocus);
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
	<div class="mx-auto flex max-w-2xl flex-col p-6">
		<header class="ignite" style="--ignite-delay: 0ms">
			<div class="flex items-start justify-between gap-4">
				<!-- The wordmark wears the board's mood (#566): the newest live run's
				     face while something is burning, the daemon's resting one when
				     nothing is. With neither it is the plain wink the landing page
				     has always shown — the frontend owns no emote table, so "no mood
				     on the wire" renders as no mood, not as a default face. -->
				<p class="eyebrow eyebrow--asis">
					<WinkWordmark frames={wordmark.frames} pitch={wordmark.pitch} />
				</p>
				<!-- Named directly as a real gap (2026-07-08): no way to end a
			     session short of clearing cookies by hand. Small on purpose
			     ("a small one somewhere") — a plain link, not a nav bar this
			     single-page dashboard doesn't otherwise have. -->
				<div class="flex items-center gap-4">
					<!-- /activity retired 2026-07-19. Its honest content — open runs,
				     queued wakes, parked respawns — is the loom's NOW seam and
				     the rack's future shelf, and its one real affordance over
				     them (filter and scroll back through history) is the
				     cloth's lens rail over its 30d window. It survived this
				     long as a page mostly because it was reporting 279 phantom
				     running runs; once #486 reaped those it rendered about
				     three rows. Folded, not re-fitted. -->
					<!-- Docs, on a signed-in surface at last (2026-08-03). The only
				     docs link in the product lived on the landing, which means it
				     vanished at the exact moment a reader acquired questions: he
				     signed up and reported "no docs link or like install it like
				     this line or anything clear you know?". A third entry beside
				     the two already here, in their grammar — still not the nav bar
				     the note above declines to build. -->
					<a
						href={DOCS_URL}
						rel="external"
						class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
						>docs</a
					>
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

		<!-- The summons strip (design-the-bolt.md §The cloth side, fork 2
		     signed): a compact one-line toast at the door, seen on load — not
		     sticky, it scrolls away naturally like everything else here. On
		     mobile the rail below fills the whole first viewport, so this has
		     to sit above it to be where the eye lands first (steer folded
		     evt-1786144375669258422-mls4). -->
		<BoltSummons unacked={unackedBoltRows} onView={viewBolts} onTakeAll={takeAllBolts} />

		<!-- The cold start, directly under the title and above everything
		     else: for an account with nothing connected every section below
		     is an empty state, so anything under the fold is under the
		     horizon. It renders while `connectedRepos` is landed and no
		     daemon has ever paired — the same source the rail and the
		     consent notice read, never a second notion of "empty" — and
		     leaves by itself once a daemon registers, not the moment a repo
		     is merely enabled (#1084). -->
		<ColdStart repos={connectedRepos} {installations} pairCommand={pairingCommand} />

		<PublishConsentNotice repos={connectedRepos} />

		<!-- The capability panel (design-capability-panel.md, build step 2):
		     the board at rest — same registry ColdStart's own detectors used
		     to answer alone, now one renderer for all of it. Sits beside
		     ColdStart rather than replacing it (that replacement, and the
		     /repos → /settings rename it unblocks, are their own signed
		     effort — design-capability-panel.md §Build order steps 3-5); this
		     is "the panel at rest + the frontier," placed where the old
		     static /repos repetition sits, above the rail so it's never
		     under the fold. -->
		<CapabilityPanel {capabilities} {connectedRepos} {pairingCommand} {now} />

		<!-- the rail, sticky (his 08-02 steer): resource truth — fuel, tank,
		     slots, the next pick — stays on top at every scroll position and
		     condenses to one line once the reader scrolls past it. This is
		     where the old order-flip died: nothing jumps when a run ignites.

		     Sticky lapses while the rack is open (#1258). The steer is about
		     the glance bar; an open rack is a temporary, full detour, not the
		     standing resource strip, and `max-h-[100svh]` alone doesn't make
		     it a *bounded* detour — sticky, it stays glued to the viewport top
		     at its full clamped height for nearly the entire rest of the
		     page's scroll range (it only releases within the last ~page-worth
		     of scroll, where its containing block actually ends), so it painted
		     over the MACHINE dock the whole time regardless of `dockTop`
		     (`railDockHeight` above fixes the *offset*; this fixes the block
		     that ate the space that offset points into). `onRackChange`
		     already lands the reader at the very top the instant it opens, so
		     `sticky` buys nothing there it doesn't already have — dropping it
		     only changes what happens once they keep scrolling: the panel
		     scrolls away like any other block, its own `overflow-y-auto` still
		     answers "is the panel's own bottom reachable", and the machine dock
		     right after it becomes reachable the ordinary way. Static, not
		     `sticky`, at scrollY 0 render identically — no jump at the
		     boundary this toggles on.

		     `overscroll-contain` (also #1258, measured rather than assumed:
		     driven live, the panel's own `scrollTop` maxed out in the first
		     wheel tick and 7600px of further wheel delta over the panel never
		     moved `window.scrollY` by one pixel) blocked the chain from "the
		     panel's own list is exhausted" to "keep scrolling the page" — the
		     literal, measured shape of "unreachable" for a reader whose
		     pointer or finger stays over the panel, which on a phone is
		     everywhere the panel is (`-mx-6`, edge to edge). Gone with
		     `sticky`, for the reader still scrolled past `max-h-[100svh]`'s
		     own overflow, same trip. -->
		<div bind:this={railSentinel} aria-hidden="true"></div>
		<div
			bind:clientHeight={railHeight}
			class="ignite {railOpen
				? 'relative'
				: 'sticky top-0'} z-40 -mx-6 max-h-[100svh] overflow-y-auto bg-stone-950/95 px-6 pt-3 pb-2 backdrop-blur-sm"
			style="--ignite-delay: 120ms"
		>
			{#if runnersData?.profiles.length === 0 && runnersWithheld}
				<WithheldNotice withheld={runnersWithheld} class="mb-2 text-sm text-amber-200" />
			{/if}
			{#if shells?.length === 0 && quotaWithheld}
				<WithheldNotice withheld={quotaWithheld} class="mb-2 text-sm text-amber-200" />
			{/if}
			<ControlStrip
				runners={runnersData}
				repos={connectedRepos}
				{shells}
				{runnersError}
				{runnersNote}
				onTap={tapWakeRunner}
				onReleaseSticky={releaseStickyRunner}
				ledgerRows={runLedgerRows}
				{scheduledWakes}
				{now}
				activeSpawns={liveRuns === null ? null : activeSpawns}
				maxSpawns={spawnMaxConcurrent}
				condensed={railCondensed}
				{onRackChange}
				{livePick}
				machineDocks={true}
			/>
		</div>
		<!-- The rail's missing height, held in flow so the page below never
		     moves when the rail changes form. Non-zero only while condensed,
		     which is only while this part of the document is off-screen. -->
		<div style={`height: ${railReserve}px`} aria-hidden="true"></div>

		<!-- the machine · the now (his 08-02 steer: "practically I think it
		     should be on top… it's the user-facing surface — looking what run is
		     doing at the moment and an overview of all of the runs"). The fall
		     (#1013: warp → machine → cloth as a run's biography) survives in the
		     tenses below, but the pulse outranks the biography at the top of the
		     page: the first question a returning reader asks is "what is
		     happening right now", and while parked the machine costs one line,
		     so the answer is free even when it is "nothing".

		     Parked, the machine is RunBlock's single line — the burning run's
		     name with its face watermarked behind it (A RUN HAS NO FACE,
		     answered where he asked it: "the face appears in the middle of the
		     block and kinda shadows the name"). Tapping the line unfolds the
		     lane in place; folding it returns the weaving items to the warp
		     stack below, where they render lit instead of resting. -->
		<!-- THE DOCK (his 2026-08-02 magnet steer, in his own correction:
			     "not the collapsed rack + oneline main runner info, as it is
			     now, but a collapsed fuel + collapsed oneline machine stuck to
			     it"). Fuel on top, the machine's one line flush beneath it, and
			     the rail's borrowed live-pick row deleted — one fact, one
			     surface.

			     Only the *head* sticks. The lane below stays in normal flow: a
			     tall panel pinned to the top of a phone is chrome eating the
			     page, and it would re-raise THE PICKER YOU CANNOT REACH (#1011)
			     by making an opened panel's own scroll position fight the dock.
			     Nothing here reads the scroll verdict to change what is open —
			     docking is visual, and the reader's expansion survives every
			     offset.

			     `top` (`dockTop` above) tracks the rail's *settled* height
			     because the rail changes form as it condenses; a pinned
			     constant would either gap or hide the head behind it, and a
			     head hidden behind the rail reads as the block having
			     vanished, which is the complaint this answers. Settled, not
			     live: reading the live `clientHeight` binding mid-transition
			     is #1169's own defect (`collapse.ts`'s `railScrollVerdict`
			     doc has the full account). -->
		<!-- Sticky travels only inside its own parent's box, so this dock is a
		     direct child of the page column — a sibling of the rail, exactly as
		     the rail is. Nested one level into the machine's own `<section>` it
		     stuck for the height of that section and then left with it, which
		     is precisely the behaviour being fixed and looked identical in a
		     static screenshot. Driven, not reasoned: the first build shipped
		     the nested version and the phone shot showed the rail alone. -->
		<!-- The seam above the block, held by the sentinel as a real box rather
		     than written as the dock's own `mt-6`. A stuck sticky box parks its
		     border box at `top` exactly — the margin goes with it — so a seam
		     living on the dock would leave the marker 24px adrift from the thing
		     it marks, and `machineDockVerdict` reads that edge to decide what a
		     tap means. Marker and gap in one element: the two edges cannot
		     disagree.

		     Sticky lapses with the rail's, while the rack is open (#1258): a
		     `top: ${dockTop}px` that tracks the rail's full-panel height is
		     exactly right for a *stuck* dock (flush under a rail also glued at
		     the viewport top) and exactly wrong for a `relative` one, where
		     `top` shifts the box off its in-flow position and would open a
		     `dockTop`-tall dead gap above it the instant the rail (and this)
		     stop being sticky. Normal flow, no offset, right where the
		     now-`relative` rail's own bottom margin puts it — the ordinary
		     reachable position this whole fix is for. -->
		<div bind:this={machineSentinel} class="h-6" aria-hidden="true"></div>
		<div
			class="ignite machine-dock {railOpen
				? 'relative'
				: 'sticky'} z-30 -mx-6 bg-stone-950/95 px-6 backdrop-blur-sm"
			style={railOpen ? '--ignite-delay: 250ms' : `--ignite-delay: 250ms; top: ${dockTop}px`}
			aria-label="the machine"
		>
			<!-- Keyed on the dock verdict, not the rail's: docking is what changes
			     this line's form — pointer or disclosure — so it is what the
			     redraw should mark. -->
			<!-- One frame, two moods: a run selected anywhere on the loom outranks
			     the lead for the head's face and name (`machineHeadRun`), so a
			     reader scrolled down to the dock still sees which run their
			     selection holds. `null` when the selection is a wake, or there is
			     none — pulse, unchanged. The lane below already renders the
			     selection inline; this only tells the docked head about it. -->
			{#key machineDocked}
				<div in:glitchReveal={{ duration: 200 }}>
					<RunBlock
						burning={burningRows}
						armed={armedRows}
						open={machineExpanded}
						docked={machineDocked}
						error={liveRunsError}
						stale={liveRunsStale}
						onToggle={onMachineToggle}
						selectedId={loomSelection?.kind === 'run' ? loomSelection.id : null}
					/>
				</div>
			{/key}
		</div>
		<section class="ignite" style="--ignite-delay: 260ms" aria-label="the machine's lane">
			{#if machineExpanded}
				<div in:glitchReveal={{ duration: 240 }}>
					<!-- The lane: armed picks falling toward the seam, the burning ones
				     sitting on it, the warp items each pick lifted carried as chips
				     on the pick itself. One tap unfolds any of them below, in the
				     same frame, in the same grammar. -->
					<div class="mt-3">
						<PickLane
							{liveRuns}
							{scheduledWakes}
							{weaving}
							{threads}
							{crossingIndex}
							{now}
							onSelect={selectFromLoom}
							{daemonMood}
							selectedId={loomSelection?.kind === 'wake' ? loomSelection.id : focusRunId}
						/>
					</div>
					{#if scheduledWakes?.length === 0 && activityWithheld}
						<WithheldNotice withheld={activityWithheld} class="mt-2 text-sm text-amber-200" />
					{/if}

					<!-- The unfold: a selected strand (or the sole live one) expands in
				     place into its run node; a selected wake into its schedule row.
				     No sibling section, no second NOW eyebrow — the stem ties the
				     panel to the seam it dropped from (the machine round:
				     `NOW · NODE` as a separate section dies). -->
					{#if loomSelection !== null || focusRunId !== null || (liveRuns?.length ?? 0) > 1}
						<div class="ignite" style="--ignite-delay: 600ms">
							<div class="mx-auto h-2 w-px bg-amber-700/60" aria-hidden="true"></div>
							{#if loomSelection !== null}
								<div class="flex justify-end">
									<button
										type="button"
										class="cursor-pointer font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
										onclick={() => (loomSelection = null)}
									>
										✕ fold
									</button>
								</div>
							{/if}
							<div class="mt-1">
								{#if loomSelection?.kind === 'wake'}
									{#if scheduledWakesError}
										<p class="mb-2 text-sm text-red-400">{scheduledWakesError}</p>
									{/if}
									{#if selectedWakes.length > 0}
										<ScheduleLane wakes={selectedWakes} {now} />
									{:else}
										<p class="text-sm text-ink-quiet">
											that wake left the schedule — it likely fired.
										</p>
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
								{:else}
									<!-- Multi-run now, nothing unfolded: tapping a card *selects*
							     it, and this same frame answers with the node panel — the
							     identical grammar a seam tap speaks. The card's old inline
							     expansion was a third rendering of the run (2026-07-20:
							     "3 visual elements for a run"); it survives only in the
							     fallbacks above, where no node can answer. -->
									<LiveRuns
										runs={liveRuns ?? []}
										stale={liveRunsStale}
										{now}
										withheld={liveRunsWithheld}
										onSelect={(id) => selectFromLoom('run', id)}
									/>
								{/if}
							</div>
						</div>
					{/if}
					{#if liveRunsError}
						<p class="mt-2 text-sm text-red-400">{liveRunsError}</p>
					{/if}
				</div>
			{/if}
		</section>

		<!-- the warp · intent (#972: the loom is the page). The fall (THE PICK,
		     2026-08-02) ordered the page as a run's biography — warp → machine
		     → cloth; the same evening's later steer amended it: the pulse
		     outranks the biography, so the machine sits above and the warp is
		     the page's second word. The warp is still intent: heat, no clock.
		     Everything with a clock — the next pick, fuel, scheduled picks,
		     live picks — is the machine's, one section above. The two futures
		     are different axes, not one store; welding them is
		     `serves:`/`taken:` references, never a merge, and the crossing
		     strip is that weld drawn.

		     The backchannel is not a sibling section but this band's needs-you
		     strip — the center element by construction (his 07-31 read: "it
		     should be one of the center elements"), because a returning reader
		     asks "what does the resident need from me?" first, and it is
		     answered without ever hiding the layers. -->
		<section class="ignite mt-6" style="--ignite-delay: 400ms" aria-labelledby="warp-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">the warp · intent</p>
					<h2 id="warp-heading" class="font-mono text-sm font-semibold text-amber-100">
						what is asked
					</h2>
				</div>
				<p class="font-mono text-[10px] text-ink-quiet">
					{surfaceData === null
						? 'stringing…'
						: `${warpLayers.length} ${warpLayers.length === 1 ? 'layer' : 'layers'} · ${warpEmberCount} ember`}
				</p>
			</div>
			<!-- The threads, named and coloured: the legend for every crossing strip
			     drawn below it, and what turns a lit tick from countable into
			     identifiable. A layer with something weaving wears its own hue and
			     a bolt — his ask, answered where he asked it. -->
			<div class="mt-1.5">
				<ThreadLegend cells={crossingCells(threads, threads)} weaving={weavingCallSigns} />
			</div>
			<!-- The flip is dead (2026-08-02): the layer stack is the standing
			     body and renders always — the old needs-you heddle *replaced*
			     it whenever items waited, so a daemon restart that resolved
			     the feeds made the warp vanish behind a tab. The needs-you
			     queue is the band's compact strip now, above the stack; feed
			     state only ever touches the strip's own chip. -->
			<div class="mt-2">
				<WarpBand
					surfaceLoaded={surfaceData !== null}
					layers={warpStackLayers}
					knownPaths={surfaceKnownPaths}
					authoredItems={authoredBackchannelItems}
					prs={prReviewQueue}
					requests={configRequests}
					feedsResolved={backchannelFeedsResolved}
					onOpenPage={openInLibrary}
					stale={prReviewQueueStale}
					{now}
					withheld={prReviewQueueWithheld}
					prError={prReviewQueueError}
					configError={configRequestsError}
				/>
			</div>
			{#if prReviewQueue?.length === 0 && prReviewQueueWithheld}
				<WithheldNotice withheld={prReviewQueueWithheld} class="mt-2 text-sm text-amber-200" />
			{/if}
		</section>

		<!-- the cloth · past (#972): what has become — the wyrd's take-up.
		     Runs as root nodes of collapsed trees over a sliding window; the
		     selvage (the cloth's self-finished edge) carries the spend→produce
		     aggregates the retired instruments section used to hold. -->
		<section class="ignite mt-10" style="--ignite-delay: 900ms" aria-labelledby="cloth-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">the cloth · past</p>
					<h2 id="cloth-heading" class="font-mono text-sm font-semibold text-amber-100">
						what has become
					</h2>
				</div>
				<p class="font-mono text-[10px] {runLedgerError ? 'text-red-400' : 'text-ink-quiet'}">
					{runLedgerError ??
						(runLedgerStale ? 'stale report' : `${loomPastWindowLabel(runLedgerWindowMs)} window`)}
				</p>
			</div>
			<div class="mt-2">
				{#if runLedgerRows !== null && runLedgerRows.length === 0 && runLedgerWithheld}
					<WithheldNotice withheld={runLedgerWithheld} />
				{:else}
					<Cloth
						rows={runLedgerRows}
						{now}
						windowMs={runLedgerWindowMs}
						stale={runLedgerStale}
						surface={surfaceData}
						{threads}
						{crossingIndex}
						unackedBolts={unackedBoltRows}
						onTakeBolt={takeOneBolt}
						onTakeAllBolts={takeAllBolts}
						{boltGlowToken}
					/>
				{/if}
			</div>
		</section>

		<section class="ignite mt-10" style="--ignite-delay: 1600ms" aria-labelledby="corpus-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">the library</p>
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
					<WorkSurface data={surfaceData} openRequest={libraryRequest} />
				{/if}
			</div>
		</section>

		<section class="ignite mt-10" style="--ignite-delay: 2100ms" aria-labelledby="billing-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">account</p>
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
