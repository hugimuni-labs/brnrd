<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import AccountDeletion from '$lib/AccountDeletion.svelte';
	import BillingPanel from '$lib/BillingPanel.svelte';
	import PickLane from '$lib/PickLane.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import RunLedgerReceipt from '$lib/RunLedgerReceipt.svelte';
	import Cloth from '$lib/Cloth.svelte';
	import {
		digestLastLookedStorageKey,
		lastLookedAnchor,
		readLastLookedAt,
		serializeLastLookedAt
	} from '$lib/digest';
	import RailGauge from '$lib/RailGauge.svelte';
	import RailBench from '$lib/RailBench.svelte';
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
	import {
		buildWarpGraph,
		readyItems,
		runTopicIndex,
		topicCounts,
		topicFaces,
		topicThreads,
		weavingRows
	} from '$lib/warpGraph';
	import HeddleRail from '$lib/HeddleRail.svelte';
	import { toggleHeddleSelection } from '$lib/heddleSelection';
	import WarpGraphView from '$lib/WarpGraphView.svelte';
	import BackchannelQueue from '$lib/BackchannelQueue.svelte';
	import { buildDerivedAsks, derivedAsksChip } from '$lib/backchannel';
	import { pickRows } from '$lib/pickLane';
	import { PRODUCE_GAUGE_LEDGER_LIMIT } from '$lib/produceGauge';
	import { CLOTH_WINDOW_MS } from '$lib/cloth';
	import { loomPastWindowLabel } from '$lib/loomBand';
	import WorkSurface from '$lib/WorkSurface.svelte';
	import {
		ReposAuthError,
		fetchRepos,
		type Capability,
		type ConnectedRepo,
		type GitHubInstallation,
		type MachinesSummary,
		type MessengerDoor
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
	import { sectionFrameLit } from '$lib/collapse';
	import { machineTapVerdict } from '$lib/machineDock';
	import {
		SCROLL_STEP_THROTTLE_MS,
		activeSectionFrom,
		initialStackClocks,
		limbDockVerdict,
		railRawVerdict,
		stackAtRest,
		stackReserve,
		stepStackClocks,
		type StackClocks
	} from '$lib/stickyStack';
	import HeddleStrip from '$lib/HeddleStrip.svelte';

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
	// Three states, not two (#480's tensed-absence family): an anonymous
	// visitor must never see the dashboard scaffolding flash before the
	// landing swaps in, and a signed-in reader must never glimpse the
	// landing. 'unknown' renders neither — the boot curtain covers it.
	let authState = $state<'unknown' | 'authed' | 'anon'>('unknown');
	let now = $state(Date.now());

	let runnersData = $state<RunnersResponse | null>(null);
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
	// design-machines-and-guests.md R1 / #1365, same `/v1/dashboard/repos`
	// fetch: account-level daemon presence, so ColdStart can tell "paired,
	// no repo enabled yet" apart from "nothing paired at all" without a
	// second round-trip to `GET /v1/machines`.
	let machines = $state<MachinesSummary | null>(null);
	// #1465, same `/v1/dashboard/repos` fetch: the registry-derived
	// connector set — every declared messenger door with its own
	// `deep_link_available` flag. `null` = an older backend that predates
	// this field, same "absent means unknown" contract `machines` already
	// set for this response — ColdStart's mobile CTA reads through to the
	// honest-intermediate copy either way.
	let messengerDoors = $state<MessengerDoor[] | null>(null);
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

	// The digest's own anchor (design-run-route.md §The home page becomes a
	// map, #1256): per-viewer, client-side, same `bolts.ts`-established
	// discipline (localStorage keyed by account id) the retired ack store
	// used.
	//
	// Visit-scoped (his 2026-08-11 read: "the highlighting… should disappear
	// on the next page reload; it shows which work was done since you last
	// visited"). The stored anchor is read once, on load, and immediately
	// re-armed to `now` — so a *second* reload starts clean — while the
	// in-memory `lastLookedAt` this visit renders against stays pinned to
	// the value that was on disk when the page opened: the glow must not
	// vanish the instant the page paints, only the instant it reloads. The
	// "caught up" press is unchanged, the mid-visit clear: storage is
	// already ahead of it by construction, so its own write only matters for
	// the in-memory anchor a reader still watches this tab.
	let lastLookedAt = $state<number | null>(null);
	let lastLookedLoadedFor = $state<string | null>(null);

	$effect(() => {
		if (!accountId || lastLookedLoadedFor === accountId) return;
		const key = digestLastLookedStorageKey(accountId);
		try {
			lastLookedAt = readLastLookedAt(localStorage.getItem(key), now);
		} catch {
			// Storage can be unavailable in a private/restricted browser — the
			// viewer just gets the fallback window every visit, never a broken
			// page.
			lastLookedAt = null;
		}
		try {
			// Re-arm immediately: this visit already has its anchor in memory
			// above, so advancing storage here (rather than waiting for
			// "caught up") is what makes the *next* reload a fresh visit.
			localStorage.setItem(key, serializeLastLookedAt(now));
		} catch {
			// Best-effort — worst case a future reload re-shows this visit's
			// highlight instead of clearing it.
		}
		lastLookedLoadedFor = accountId;
	});

	function markCaughtUp() {
		lastLookedAt = now;
		if (!accountId) return;
		try {
			localStorage.setItem(digestLastLookedStorageKey(accountId), serializeLastLookedAt(now));
		} catch {
			// Best-effort — the live anchor still governs this tab for this visit.
		}
	}

	let configRequests = $state<ConfigChangeRequestItem[] | null>(null);
	let configRequestsError = $state<string | null>(null);

	let surfaceData = $state<SurfaceResponse | null>(null);
	let surfaceError = $state<string | null>(null);

	let surfaceKnownPaths = $derived(new Set((surfaceData?.files ?? []).map((f) => f.path)));
	// The warp as a graph (2026-08-11 round): items under `surface/warp/`,
	// topics under `surface/topics/`, discovered from the same corpus feed
	// the corpus browser already reads — no new endpoint, another reader of
	// one fetch. Topics are the filter axis (the heddles); blocked/ready are
	// derived from the `needs:` edges; the runes now hash from canonical
	// topic ids, so every mark on this page is stable across set changes.
	let warpGraphData = $derived(buildWarpGraph(surfaceData?.files ?? []));
	let topicThreadList = $derived(topicThreads(warpGraphData));
	let topicCountsMap = $derived(topicCounts(warpGraphData));
	let warpReadyCount = $derived(readyItems(warpGraphData).length);
	// The derived half of needs-you (PR review queue + config requests) —
	// authored asks live in the warp as decision/preparation items now. A
	// draft PR is filtered out by buildDerivedAsks itself (not here), so
	// this count, the strip's visibility below, and its chip all agree.
	let derivedNeedsItems = $derived(buildDerivedAsks(prReviewQueue ?? [], configRequests ?? []));
	let needsOpen = $state(false);
	// The heddle selection: canonical topic ids lit; null = all (default).
	// Per-viewer, per-account, persisted like the digest anchor.
	let heddleSelection = $state<Set<string> | null>(null);
	let heddleLoadedFor = $state<string | null>(null);
	$effect(() => {
		if (!accountId || heddleLoadedFor === accountId) return;
		try {
			const raw = localStorage.getItem(`brnrd.heddles.${accountId}`);
			const parsed = raw ? JSON.parse(raw) : null;
			heddleSelection = Array.isArray(parsed)
				? new Set(parsed.filter((id) => typeof id === 'string'))
				: null;
		} catch {
			heddleSelection = null;
		}
		heddleLoadedFor = accountId;
	});
	function persistHeddles(next: Set<string> | null) {
		heddleSelection = next;
		if (!accountId) return;
		try {
			if (next === null) localStorage.removeItem(`brnrd.heddles.${accountId}`);
			else localStorage.setItem(`brnrd.heddles.${accountId}`, JSON.stringify([...next]));
		} catch {
			// Storage unavailable — the selection still governs this tab.
		}
	}
	function toggleHeddle(id: string) {
		persistHeddles(
			toggleHeddleSelection(
				heddleSelection,
				id,
				topicThreadList.map((thread) => thread.canonicalId)
			)
		);
	}
	function allHeddles() {
		persistHeddles(null);
	}
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
	let weaving = $derived(weavingRows(warpGraphData, liveRunIds));
	// THE CROSSING (`crossing.ts`): the warp threads in authored order, and
	// run id → the ones each run lifted, read off the `taken:` rows the weld
	// already writes, unioned with each run's own `topics.md` claim
	// (`runTopicIndex`'s other door, for a run that took no item). One index,
	// three readers — the warp header's legend, the
	// pick lane's rows, the cloth's lines — so one alphabet travels the whole
	// page: same threads, same cells, same width, wherever a strip is drawn.
	// That shared vocabulary is the answer to "temporal repeating instead of
	// referencing": a run and the intent it served point at each other through
	// the strip, and neither re-lists the other. (The strips also share an x
	// *within* the lane; the cloth's rows wrap, so there they do not — the
	// alphabet is the claim, not the column.)
	// The machine's own row set. Same `pickRows` the lane draws from — one
	// computation, two readers (the parked run block, the lane), so no two
	// surfaces can disagree about which pick is burning. (A third reader,
	// the rail's own slim-bar line (`ControlStrip`'s `livePick`), was
	// removed 2026-08-19 — dead once the machine dock always sits under the
	// rail on this page, so the branch that would have shown it never fired.)
	let machineRows = $derived(pickRows({ liveRuns, scheduledWakes, now }));
	let burningRows = $derived(machineRows.filter((row) => row.phase === 'picking'));
	let armedRows = $derived(machineRows.filter((row) => row.phase === 'armed'));
	let threads = $derived(topicThreadList.map((thread) => thread.canonicalId));
	// Which topics have an item weaving right now — the answer to "which one
	// is being worked", rendered on the heddle rail where the question gets
	// asked rather than only on the run that is doing it.
	let weavingCallSigns = $derived(new Set(weaving.map((row) => row.callSign).filter(Boolean)));
	let crossingIndex = $derived(runTopicIndex(warpGraphData, surfaceData?.files ?? []));
	let topicFaceMap = $derived(topicFaces(warpGraphData));
	// All three feeds resolved (loaded or errored) — until then the needs
	// strip's sum is a partial read, and rendering it as a verdict is the
	// measured 20 → "clear" → 4 flicker. `derivedNeedsItems.length === 0`
	// alone cannot tell "feeds not yet fetched" from "genuinely nothing
	// waiting"; only the feed handles can. The strip's chip is the only
	// place this state renders — the layer stack below it never hears
	// about it.
	let derivedAsksFeedsResolved = $derived(
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

	// THE STACK THAT OWNS ITS GEOMETRY (w-48, `design-the-sticky-stack.md`;
	// case file #1169 · #1258 · #1325 · #1328 · the post-#1328 tap-eater).
	// One sticky container — rail, docked heddle copy, machine head, section
	// label — replaces the hand-stepped clocks, six settled-height samples,
	// three reserve spacers, and two JS-computed `top:` styles that used to
	// coordinate three independently-positioned sticky elements. CSS layout
	// owns geometry; JavaScript owns only booleans (`stickyStack.ts` has the
	// argument). The felt behavior — collapse commits `SCROLL_SETTLE_MS`
	// after the first qualifying crossing, expansion is immediate, every
	// boundary carries a dead band — survives verbatim through the same
	// `scrollClockTick` the old machinery used, now fed by a throttled
	// live-rect step instead of a per-frame scroll handler.
	//
	// Sentinels, all flow-stable by construction:
	// - `releaseSentinel` (before the container): the stack's own top — the
	//   rail un-condenses when it comes back within 8px of the viewport top.
	// - `condenseSentinel` (after the reserve spacer): the *at-rest* stack
	//   bottom — the spacer holds `rest − live`, so this line does not move
	//   when the rail changes form, and the rail condenses only once the
	//   whole full stack has provably scrolled past (THE BOUNDARY THAT
	//   FLICKERED's dead band, kept).
	// - `heddleSentinel` (the heddle rail's home in the warp) and
	//   `machineSentinel` (the lane's home): each limb docks when its home
	//   crosses the stack's *live* bottom edge, read directly off the
	//   container at each step. A late step shifts *when* a limb docks by
	//   pixels of scroll; it can never shift *where anything paints* —
	//   that asymmetry is the whole design.
	let stackEl = $state<HTMLElement | null>(null);
	let releaseSentinel = $state<HTMLElement | null>(null);
	let condenseSentinel = $state<HTMLElement | null>(null);
	let heddleSentinel = $state<HTMLElement | null>(null);
	let machineSentinel = $state<HTMLElement | null>(null);
	// Section headings, bound once each mounts — observed by the same
	// boundary observer the limb sentinels use (a standard scroll-spy), so
	// `activeSection` updates at crossings rather than per-frame reads.
	let warpHeadingEl = $state<HTMLElement | null>(null);
	let clothHeadingEl = $state<HTMLElement | null>(null);
	let corpusHeadingEl = $state<HTMLElement | null>(null);
	let billingHeadingEl = $state<HTMLElement | null>(null);
	let benchOpen = $state(false);
	let clocks = $state<StackClocks>(initialStackClocks());
	let railCondensed = $derived(clocks.rail.settled);
	let heddleDocked = $derived(clocks.heddle.settled);
	let machineDocked = $derived(clocks.lane.settled);
	// The stack's own collapsed verdict — `machineDocked` already implies the
	// rail is condensed above it (each dock boundary sits strictly after the
	// one above it), so one flag names "the stack is fully collapsed" for
	// `sectionFrameLit`'s two readers (his 2026-08-11 report, "it never
	// un-highlights" — the border must never light without the stack
	// actually being collapsed).
	let stackCollapsed = $derived(machineDocked);
	let activeSection = $state<{ id: string; label: string } | null>(null);
	let showSectionLabel = $derived(stackCollapsed && activeSection !== null);
	// The stack's live height (ResizeObserver) — also the scroll-margin every
	// warp item's `<li>` reads via `--sticky-stack-h`, so a followed `#w-N`
	// link lands below whatever the stack currently paints.
	let stickyStackHeight = $state(0);
	// The one surviving settled sample, and the design's whole risk budget:
	// re-sampled on every qualifying resize (never frozen), it feeds only the
	// spacer below the container — see `stackReserve`'s own doc.
	let stackRestHeight = $state(0);
	let stackReservePx = $derived(stackReserve(stackRestHeight, stickyStackHeight));
	// `onRackChange` needs to re-step the clocks the instant the rack opens
	// (un-docking is immediate, never debounced); the wiring effect below
	// installs the real function.
	let requestStackStep: () => void = () => {};

	$effect(() => {
		if (typeof window === 'undefined') return;
		const stack = stackEl;
		const release = releaseSentinel;
		const condense = condenseSentinel;
		const heddleHome = heddleSentinel;
		const laneHome = machineSentinel;
		const headings = [warpHeadingEl, clothHeadingEl, corpusHeadingEl, billingHeadingEl].filter(
			(el): el is HTMLElement => el !== null
		);
		if (!stack || !release || !condense || !heddleHome || !laneHome) return;

		// Local authority; the reactive `clocks` is assigned only on change
		// (reference-identity dirtying). `step` runs from listeners and
		// timers — async, so its reads of `benchOpen` are untracked and this
		// effect re-runs only when an element binding changes.
		//
		// Raw verdicts are computed from live rects inside `step`, never
		// cached from observer entries. IntersectionObserver was driven
		// first and measured out: an instant jump (deep link, fling, a
		// programmatic scroll) teleports a sentinel from below the viewport
		// to above it with `isIntersecting` false -> false — no state
		// change, no callback, and a cached "above" map holds its stale
		// answer indefinitely. Eight rect reads per step, at most every
		// SCROLL_STEP_THROTTLE_MS, is the honest price; the old machinery
		// paid more per animation frame.
		let current: StackClocks = initialStackClocks();
		let settleTimer: ReturnType<typeof setTimeout> | null = null;
		let throttleTimer: ReturnType<typeof setTimeout> | null = null;

		const step = () => {
			if (settleTimer !== null) {
				clearTimeout(settleTimer);
				settleTimer = null;
			}
			const now = Date.now();
			// The stack's live bottom edge is the limb boundary in both
			// regimes for free: pinned, it is the painted stack height; at
			// rest, it is the stack's own flow bottom — below every limb
			// home, so nothing docks at rest without arithmetic saying so.
			const stackBottom = stack.getBoundingClientRect().bottom;
			const raws = {
				rail: railRawVerdict({
					condenseAbove: condense.getBoundingClientRect().top < 0,
					// The rail's own 8px release slack, as it always was:
					// un-condense only once the stack's top is back within
					// 8px of the viewport's.
					releaseAbove: release.getBoundingClientRect().top < -8,
					condensed: current.rail.settled
				}),
				heddle: limbDockVerdict({
					homeTop: heddleHome.getBoundingClientRect().top,
					stackBottom,
					docked: current.heddle.settled
				}),
				lane: limbDockVerdict({
					homeTop: laneHome.getBoundingClientRect().top,
					stackBottom,
					docked: current.lane.settled
				})
			};
			const result = stepStackClocks(current, raws, benchOpen, now);
			current = result.clocks;
			if (result.changed) clocks = result.clocks;
			const nextSection = activeSectionFrom(
				headings.map((el) => ({
					id: el.id,
					label: el.textContent?.trim() ?? '',
					above: el.getBoundingClientRect().top < stackBottom
				}))
			);
			if (nextSection?.id !== activeSection?.id) activeSection = nextSection;
			if (result.nextDeadline !== null) {
				settleTimer = setTimeout(step, Math.max(0, result.nextDeadline - now));
			}
		};
		requestStackStep = step;

		// Trailing-edge throttle: at most one step per window, and always
		// one after the last event — an instant jump's final position is
		// never missed, which is the exact hole the observer version had.
		const scheduleStep = () => {
			if (throttleTimer !== null) return;
			throttleTimer = setTimeout(() => {
				throttleTimer = null;
				step();
			}, SCROLL_STEP_THROTTLE_MS);
		};

		const resizeObserver = new ResizeObserver((entries) => {
			const height = entries[entries.length - 1]?.contentRect.height;
			if (typeof height !== 'number') return;
			const rounded = Math.round(height);
			if (rounded !== stickyStackHeight) stickyStackHeight = rounded;
			if (
				stackAtRest({
					railOpen: benchOpen,
					railCondensed: current.rail.settled,
					heddleDocked: current.heddle.settled,
					machineDocked: current.lane.settled
				}) &&
				rounded > 0 &&
				rounded !== stackRestHeight
			) {
				stackRestHeight = rounded;
			}
			scheduleStep();
		});
		resizeObserver.observe(stack);

		window.addEventListener('scroll', scheduleStep, { passive: true });
		window.addEventListener('resize', scheduleStep, { passive: true });
		// `untrack`, load-bearing (THE RAIL THAT COULDN'T DECIDE, 2026-08-13):
		// every other `step()` call runs from listeners and timers — async, so
		// its `benchOpen`/`activeSection` reads are untracked, exactly as the
		// comment at the top of this effect promises. This first call is the
		// one synchronous exception: unwrapped, its reads made `activeSection`
		// a dependency of this whole wiring effect, so every scroll-spy
		// crossing (any topic split) tore the machinery down and rebuilt it
		// with `initialStackClocks()` — un-collapsing rail, heddles, and label
		// for one settle window. The transient expansion moved the stack's
		// bottom edge back across the very heading that had just crossed it,
		// flipping `activeSection` back, and the loop self-sustained: the rail
		// flapped full↔slim at ~12Hz while the reader held still at a section
		// boundary (`repro/repro3.mjs` measures exactly this).
		untrack(step);

		return () => {
			requestStackStep = () => {};
			window.removeEventListener('scroll', scheduleStep);
			window.removeEventListener('resize', scheduleStep);
			resizeObserver.disconnect();
			if (settleTimer !== null) clearTimeout(settleTimer);
			if (throttleTimer !== null) clearTimeout(throttleTimer);
		};
	});

	// THE RESERVE'S OWN RACE (regression after #1331, this fix): the effect
	// above only ever updated `stickyStackHeight` from the ResizeObserver,
	// which fires *after* the browser has already laid out — and can
	// already have painted — the DOM mutation a dock/condense flip causes.
	// `stackReserve`'s own doc called this lag "the design's whole risk
	// budget" and expected at most "one scroll jump near the top." Measured
	// under a fast fling (repro/repro2.mjs, kb/design-the-sticky-stack.md):
	// the gap is wider than that. For the one paint between the boolean
	// flip and the next ResizeObserver notification, `stackReservePx` is
	// still built from the *stale pre-transition* height, so the document
	// is transiently shorter than it should be by exactly the stack's own
	// shrink — and a reader already scrolled past that point gets their
	// scroll position yanked mid-flight. It happens once per limb that
	// docks/condenses in the burst (up to three, crossed together on a fast
	// enough scroll — the "after a second" the report names), and a scroll
	// position yanked out from under an in-flight momentum scroll is
	// exactly the regime that leaves a `position: sticky` + backdrop-filter
	// layer showing a stale compositor frame on mobile Safari (the docked
	// strip "painted over" list content, clipped mid-paint).
	//
	// Re-measuring here, keyed on the same booleans that drive the docked
	// content's mount/unmount, closes the gap structurally rather than
	// racing it: Svelte runs `$effect`s after the DOM has been patched for
	// the reactive change that triggered them, so by the time this body
	// runs, `railCondensed`/`heddleDocked`/`machineDocked` and the DOM they
	// drove are already in agreement — same tick as the mutation, not the
	// next ResizeObserver notification. The ResizeObserver above still
	// owns the general case (viewport width, font load, content reflow —
	// resizes these booleans don't predict); this effect only front-runs it
	// for the resizes the stack's own state already knows are coming.
	$effect(() => {
		// Explicit reads so the effect re-runs on each transition, not once.
		void railCondensed;
		void heddleDocked;
		void machineDocked;
		void benchOpen;
		if (typeof window === 'undefined' || !stackEl) return;
		const height = Math.round(stackEl.getBoundingClientRect().height);
		if (height > 0 && height !== stickyStackHeight) stickyStackHeight = height;
	});

	// The section frame's own quiet half of the ask — "the active section's
	// frame also lights subtly… keep it quiet, the header line is the loud
	// half". Same idiom `WarpGraphView`'s own item rows already use for a
	// live-held frame (`border-l-2`, colour swapped rather than the border
	// itself appearing) — the border is always present, always transparent
	// until lit, so toggling it never touches the section's box model the
	// way adding/removing padding would.
	function sectionActive(id: string): boolean {
		return sectionFrameLit(stackCollapsed, activeSection?.id ?? null, id);
	}

	// His proposal, verbatim (2026-08-03): "when it's expanded, it should just
	// somehow go to the top of the page. And when it's collapsed, go back if
	// it's possible." w-68 (the gauge/bench split) changed *why* this still
	// matters: the gauge is always small and always `sticky top-0` now, so
	// opening the bench can no longer pin an unreachable-bottomed panel over
	// the viewport (the defect this originally fixed). What survives is the
	// plain courtesy — the bench mounts in normal flow right after the
	// (tiny, sticky) gauge, near the top of the document, so opening it while
	// scrolled deep into the cloth would otherwise insert a panel the reader
	// cannot see without scrolling up by hand. `$state`, matching
	// `machineReturnY` below: neither value drives a template read, so the
	// rune buys no reactivity either one actually needs.
	let railReturnY = $state<number | null>(null);
	function onBenchToggle() {
		const open = !benchOpen;
		benchOpen = open;
		// Un-docking is immediate, never debounced — step the clocks in the
		// same act, before the smooth scroll below has moved anything.
		requestStackStep();
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
			window.scrollTo({ top: Math.max(0, home - stickyStackHeight), behavior: 'smooth' });
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
				machines = repos.machines ?? null;
				messengerDoors = repos.messenger_doors ?? null;
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
	<div
		class="mx-auto flex max-w-2xl flex-col p-6"
		style={`--sticky-stack-h: ${stickyStackHeight}px`}
	>
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

		<!-- The digest block is gone (2026-08-11, his ask: it was a redirect
		     onto a run and repeated after the cloth). Its "since you looked"
		     anchor survives below: cloth rows newer than the last visit wear
		     the brighter ground, and "caught up" sits in the cloth's header. -->

		<!-- The cold start, directly under the title and above everything
		     else: for an account with nothing connected every section below
		     is an empty state, so anything under the fold is under the
		     horizon. It renders while `connectedRepos` is landed and no
		     daemon has ever paired — the same source the rail and the
		     consent notice read, never a second notion of "empty" — and
		     leaves by itself once a daemon registers, not the moment a repo
		     is merely enabled (#1084). -->
		<ColdStart
			repos={connectedRepos}
			{installations}
			pairCommand={pairingCommand}
			{machines}
			{messengerDoors}
		/>

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

		<div bind:this={releaseSentinel} class="h-px -mb-px" aria-hidden="true"></div>
		<!-- THE STACK (w-48, `design-the-sticky-stack.md`): gauge, docked heddle
		     copy, machine head, section label — one sticky container, so every
		     slot's position is CSS layout and none of them can be painted at a
		     wrong document coordinate (the tap-eater class this replaces).
		     Unconditionally `sticky top-0` since w-68 (the gauge/bench split):
		     the old `relative` fallback existed because an open rack could pin a
		     panel taller than the viewport with its own bottom unreachable — the
		     gauge cannot do that any more (one line, fixed height, by
		     construction), and the bench that replaced the rack's picking surface
		     mounts *outside* this container entirely, so nothing tall is ever a
		     child of a sticky box here. See `RailBench` below, past the docking
		     sentinels. -->
		<div bind:this={stackEl} class="sticky top-0 z-40">
			<!-- the gauge: resource truth — next pick, fuel, tank — one line,
			     always, whatever the scroll position. Bottom padding is
			     reclaimed while the stack has condensed past it (his magnet
			     steer: "could we remove the space between them, almost at
			     least, when they are collapsed and on the top?") so the docked
			     strips beneath sit flush. -->
			<!-- #1281: no per-lane "paused —" restated here — `runnersWithheld` /
			     `quotaWithheld` are the same account-level publish-scope fact
			     `PublishConsentNotice` already stated once, above, in the variant
			     that carries the action (`set a scope`). -->
			<div
				class="ignite -mx-6 bg-stone-950/95 px-6 pt-3 backdrop-blur-sm {railCondensed && !benchOpen
					? 'pb-0'
					: 'pb-2'}"
				style="--ignite-delay: 120ms"
			>
				<RailGauge
					runners={runnersData}
					{shells}
					ledgerRows={runLedgerRows}
					{scheduledWakes}
					{now}
					activeSpawns={liveRuns === null ? null : activeSpawns}
					maxSpawns={spawnMaxConcurrent}
					{benchOpen}
					{onBenchToggle}
				/>
			</div>

			<!-- the heddles, docked (his 2026-08-12 follow-up: "since it acts as
			     a filter for all the surfaces, and I might wanna change the
			     filters as I am looking at the cloth for example"). Rail →
			     heddles → machine: the heddles lens the warp, the machine lane,
			     and the cloth below them, but never the rail (resource truth,
			     unfiltered). A thin SEAM, not a third box: different tint,
			     hairline borders, tighter padding, so it reads as a lens laid
			     between instruments.

			     Same control as the home strip below — `HeddleStrip` renders the
			     SAME `heddleSelection` through the SAME `toggleHeddle`/
			     `allHeddles`, never a forked copy. Mounted only while docked
			     (the home strip is the control at rest); unmounting happens
			     off-screen behind the dead band, and the stack's own layout
			     absorbs the footprint — no reserve spacer, which also retires
			     the phantom rest-gap the old reserve left behind after the
			     first-ever dock. No `.ignite` here on purpose — that class is
			     "state birth only" (`layout.css`), and this box mounts on every
			     docking crossing. -->
			{#if heddleDocked}
				<div
					class="-mx-6 flex flex-wrap items-baseline gap-x-2 gap-y-1 border-y border-stone-800/60 bg-stone-900 px-6 py-1.5"
					aria-label="the heddles · lens"
				>
					<HeddleStrip
						threads={topicThreadList}
						selected={heddleSelection}
						weaving={weavingCallSigns}
						onToggle={toggleHeddle}
						onAll={allHeddles}
					/>
				</div>
			{/if}

			<!-- the machine · the now (his 08-02 steer: "practically I think it
			     should be on top… it's the user-facing surface"). While parked
			     the machine costs one line, so "what is happening right now" is
			     free even when the answer is "nothing". The head lives in the
			     stack permanently (his 2026-08-12 sign-off on the w-48 design):
			     its old sibling-with-computed-`top:` arrangement re-pinned it
			     after ~48px of scroll anyway, and the offset arithmetic was the
			     tap-eater's whole species. Only the head is chrome — the lane
			     below stays in normal flow (#1011: a tall body is never pinned),
			     and nothing here reads a scroll verdict to change what is open.
			     At rest the head keeps the old seam as its own margin; docked,
			     the margin collapses and the head magnets flush under the rail
			     (`machineDockTop`'s old 8px reclaim, now just layout). -->
			<div
				class="ignite machine-dock -mx-6 bg-stone-950/95 px-6 backdrop-blur-sm {machineDocked
					? ''
					: 'mt-6'}"
				style="--ignite-delay: 250ms"
				aria-label="the machine"
			>
				<!-- Keyed on the dock verdict: docking is what changes this line's
				     form — pointer or disclosure — so it is what the redraw marks.
				     A run selected anywhere on the loom outranks the lead for the
				     head's face and name (`machineHeadRun`). -->
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
							{crossingIndex}
							topicFaces={topicFaceMap}
						/>
					</div>
				{/key}
				<!-- The bar that knows the section: the stack's own footer line,
				     ember because this one line answers "where am I". Renders only
				     once the stack has actually collapsed and a tracked heading
				     has scrolled up to meet it. -->
				{#if showSectionLabel && activeSection}
					<a
						href={`#${activeSection.id}`}
						class="-mx-1 mt-1 flex items-baseline gap-1.5 border-t border-stone-800/80 px-1 pt-1 pb-1 font-mono text-[10px] tracking-wide text-amber-300/90 hover:text-amber-200"
						onclick={(event) => {
							event.preventDefault();
							document
								.getElementById(activeSection!.id)
								?.scrollIntoView({ behavior: 'smooth', block: 'start' });
						}}
					>
						<span aria-hidden="true">↓</span>
						{activeSection.label}
					</a>
				{/if}
			</div>
		</div>
		<!-- The one surviving spacer (`stackReserve`): a sticky container is
		     still in flow, so without this the rail condensing would move the
		     document under a scrolled reader. Holds `rest − live`, non-zero
		     only while the stack's full form is provably off-screen. It
		     positions nothing — a stale value is one scroll jump near the top,
		     never a mis-painted strip. -->
		<div style={`height: ${stackReservePx}px`} aria-hidden="true"></div>
		<!-- The condense boundary: flow-stable at the at-rest stack bottom
		     (the spacer above is exactly what holds it still). The rail
		     condenses once this line scrolls above the viewport top, and
		     un-condenses only when `releaseSentinel` returns — THE BOUNDARY
		     THAT FLICKERED's dead band, as a sentinel pair. -->
		<div bind:this={condenseSentinel} class="h-px -mb-px" aria-hidden="true"></div>
		<!-- The lane's home: the machine head's docked form (pointer vs
		     disclosure, `machineTapVerdict`) keys on this line crossing the
		     stack's live bottom — the geometric fact a tap's meaning turns on,
		     measured off a sibling in normal flow because the head itself is
		     always stuck. -->
		<div bind:this={machineSentinel} class="h-px -mb-px" aria-hidden="true"></div>
		<!-- THE BENCH (w-68, signed 2026-08-19): project · environment · core,
		     mounted only on request, outside the sticky stack entirely — opening
		     it never touches the gauge's own layout or the docking sentinels
		     above, which is the whole point of pulling it out of THE STACK. Its
		     own `panel`, free to be as tall as the catalog needs. -->
		{#if benchOpen}
			<RailBench
				runners={runnersData}
				repos={connectedRepos}
				{runnersError}
				{runnersNote}
				{now}
				onTap={tapWakeRunner}
				onReleaseSticky={releaseStickyRunner}
			/>
		{/if}
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
							topicFaces={topicFaceMap}
							selectedTopics={heddleSelection}
							{now}
							onSelect={selectFromLoom}
							{daemonMood}
							selectedId={loomSelection?.kind === 'wake' ? loomSelection.id : focusRunId}
						/>
					</div>
					<!-- #1281: no per-lane "paused —" restated here — see the gauge's
					     own note above `<RailGauge>`; same fact, same fix. -->

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
											{crossingIndex}
											topicFaces={topicFaceMap}
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
		<section
			class="ignite mt-6 border-l-2 pl-3 transition-colors duration-300 {sectionActive(
				'warp-heading'
			)
				? 'border-amber-500/40'
				: 'border-transparent'}"
			style="--ignite-delay: 400ms"
			aria-labelledby="warp-heading"
		>
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">the warp · intent</p>
					<h2
						bind:this={warpHeadingEl}
						id="warp-heading"
						class="font-mono text-sm font-semibold text-amber-100"
					>
						what is asked
					</h2>
				</div>
				<p class="font-mono text-[10px] text-ink-quiet">
					{surfaceData === null
						? 'stringing…'
						: `${topicThreadList.length} topic${topicThreadList.length === 1 ? '' : 's'} · ${warpReadyCount} ready`}
				</p>
			</div>
			<!-- The heddles: the topic rail — the Photoshop-layers filter axis.
			     Collapsed it is the legend (every topic's rune, lit or dim, each
			     a working toggle); expanded it is the flat topic list with
			     ready/held counts. The lit set lenses the warp below and the
			     cloth after it.

			     The heddle rail's true home — nothing about this box changes
			     shape with scroll (unlike the rail above, it never needs its
			     own reserve spacer). `heddleSentinel` marks the top edge the
			     docking verdict measures; the docked copy renders far above,
			     between the rail and the machine dock. -->
			<div bind:this={heddleSentinel} aria-hidden="true"></div>
			<div class="mt-1.5">
				<HeddleRail
					threads={topicThreadList}
					counts={topicCountsMap}
					selected={heddleSelection}
					weaving={weavingCallSigns}
					onToggle={toggleHeddle}
					onAll={allHeddles}
				/>
			</div>
			<!-- The derived half of needs-you: PR review + config approvals —
			     feeds the daemon derives, not items anyone authored. Authored
			     asks are decision/preparation items in the warp itself now, so
			     this strip renders only when something derived actually waits. -->
			{#if derivedNeedsItems.length > 0 || prReviewQueueError || configRequestsError}
				<div class="subpanel mt-2 px-3 py-2 text-xs">
					<button
						type="button"
						class="flex w-full cursor-pointer flex-wrap items-baseline gap-x-2 text-left"
						aria-expanded={needsOpen}
						onclick={() => (needsOpen = !needsOpen)}
					>
						<span class="font-mono text-[10px] text-ink-quiet" aria-hidden="true"
							>{needsOpen ? '▾' : '▸'}</span
						>
						<span class="font-mono text-[11px] tracking-wide text-amber-200 uppercase"
							>needs you</span
						>
						<span class="font-mono text-[10px] text-ink-quiet"
							>· {derivedAsksChip(derivedAsksFeedsResolved, derivedNeedsItems.length)}</span
						>
					</button>
					{#if needsOpen}
						<div class="mt-2">
							{#if prReviewQueueError}
								<p class="mb-2 text-sm text-red-400">{prReviewQueueError}</p>
							{/if}
							{#if configRequestsError}
								<p class="mb-2 text-sm text-red-400">{configRequestsError}</p>
							{/if}
							<BackchannelQueue
								prs={prReviewQueue ?? []}
								requests={configRequests ?? []}
								stale={prReviewQueueStale}
								{now}
								withheld={prReviewQueueWithheld}
							/>
						</div>
					{/if}
				</div>
			{/if}
			<!-- The graph: unblocked items colorful on top — glance, decide or
			     do — blocked ones greyed below, live-held ones framed in place. -->
			<div class="mt-2">
				{#if surfaceData === null}
					<p class="text-sm text-ink-quiet">stringing…</p>
				{:else}
					<WarpGraphView
						graph={warpGraphData}
						selected={heddleSelection}
						{liveRunIds}
						knownPaths={surfaceKnownPaths}
						onOpenPage={openInLibrary}
					/>
				{/if}
			</div>
			<p class="mt-2 font-mono text-[10px] text-ink-mute">
				<a href={resolve('/warp')} class="hover:text-stone-300"
					>all items · live &amp; completed →</a
				>
			</p>
		</section>

		<!-- the cloth · past (#972): what has become — the wyrd's take-up.
		     Runs as root nodes of collapsed trees over a sliding window; the
		     selvage (the cloth's self-finished edge) carries the spend→produce
		     aggregates the retired instruments section used to hold. -->
		<section
			class="ignite mt-10 border-l-2 pl-3 transition-colors duration-300 {sectionActive(
				'cloth-heading'
			)
				? 'border-amber-500/40'
				: 'border-transparent'}"
			style="--ignite-delay: 900ms"
			aria-labelledby="cloth-heading"
		>
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">the cloth · past</p>
					<h2
						bind:this={clothHeadingEl}
						id="cloth-heading"
						class="font-mono text-sm font-semibold text-amber-100"
					>
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
						topicFaces={topicFaceMap}
						selectedTopics={heddleSelection}
						newSince={lastLookedAnchor(lastLookedAt, now)}
						onCaughtUp={markCaughtUp}
					/>
				{/if}
			</div>
		</section>

		<section
			class="ignite mt-10 border-l-2 pl-3 transition-colors duration-300 {sectionActive(
				'corpus-heading'
			)
				? 'border-amber-500/40'
				: 'border-transparent'}"
			style="--ignite-delay: 1600ms"
			aria-labelledby="corpus-heading"
		>
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">the library</p>
					<h2
						bind:this={corpusHeadingEl}
						id="corpus-heading"
						class="font-mono text-sm font-semibold text-amber-100"
					>
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

		<section
			class="ignite mt-10 border-l-2 pl-3 transition-colors duration-300 {sectionActive(
				'billing-heading'
			)
				? 'border-amber-500/40'
				: 'border-transparent'}"
			style="--ignite-delay: 2100ms"
			aria-labelledby="billing-heading"
		>
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">account</p>
					<h2
						bind:this={billingHeadingEl}
						id="billing-heading"
						class="font-mono text-sm font-semibold text-amber-100"
					>
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
