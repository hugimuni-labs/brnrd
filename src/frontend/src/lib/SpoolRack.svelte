<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import { liveSticky, type RunnerProfile, type RunnerSticky, type WakeRequest } from './runners';
	import { stickyCountdown } from './railGauge';
	import {
		defaultShell,
		deadShellReason,
		isTappable,
		offReasonOf,
		groupByShell
	} from './spoolRack';
	import {
		IDLE_ROW,
		LIVE_CLAIM,
		OFF_MARK,
		OFF_ROW,
		SELECTED_PINNED,
		SELECTED_REQUESTED
	} from './stateChrome';

	// #328 spool rack. You don't set a being's body with a dropdown; the
	// rack shows who *can* wake and which spool is threaded (the pin).
	// A tap parks a one-shot "next wake on this profile" request
	// (#328 tap-to-request): no confirm modal — the tapper is the account
	// owner approving their own ask — and cancelable until the wake fires.
	// A durable default change stays a conversation with the resident
	// (config-change request → approve page). No selector here, on purpose.
	//
	// Every tap means one thing: "next wake here". Canceling a parked
	// request = tapping the default row (still pinned, still visible) —
	// not re-tapping the requested row, which silently toggled a request
	// away on first live use (2026-07-11). The page owns the routing;
	// this component just reports which row was tapped.
	//
	// w-68 rework (2026-08-19, the gauge/bench split), two of his steers
	// taken mid-flight:
	//
	// - **shell, then core.** The rack used to flatten every profile into
	//   one column grouped only by a shell *header* — N×M rows growing
	//   multiplicatively with the catalog. It is a two-stage picker now:
	//   tabs select the shell, and only the selected shell's cores render
	//   as rows below. `groupByShell` already computed this structure;
	//   this component used to throw it away.
	// - **the row shows an answer, never a doubt.** `offerabilityOf`
	//   resolves the tri-state availability plus every staleness signal to
	//   one binary before this component ever sees it — a row is offerable
	//   or it is off, and "off" always renders *designed* off (dashed,
	//   full-opacity ink, a stated reason when the daemon gave one), never
	//   a dimmed copy of a live row and never a third "stale" state.
	interface Props {
		profiles: RunnerProfile[];
		defaultProfile: string | null;
		stale: boolean;
		wakeRequest: WakeRequest | null;
		/** #932's conversation-sticky: the profile that actually answers the
		 *  bound thread's wakes until it expires. Rendered as a timered chip
		 *  — the rack must not show the pin as the whole truth while this
		 *  record decides (the 2026-08-08 "core tap is lying" defect). */
		sticky?: RunnerSticky | null;
		now?: number;
		onTap?: (profileName: string) => void;
		/** The sticky's exit: drop it now instead of waiting out the TTL. */
		onReleaseSticky?: () => void;
		/** The fuel gauge's own "press a provider row" ask
		 *  (design-resident-field.md §Settings, fuel, and the next dispatch):
		 *  open the rack already tabbed to that provider's shell, since a
		 *  provider *is* the shell family this rack already groups by. Read
		 *  once per change, not fought on every re-render — a reader who then
		 *  taps a different tab keeps that choice until the next expand. */
		focusShell?: string | null;
	}

	let {
		profiles,
		defaultProfile,
		stale,
		wakeRequest,
		sticky = null,
		now = Date.now(),
		onTap,
		onReleaseSticky,
		focusShell = null
	}: Props = $props();

	let stickyLive = $derived(liveSticky(sticky, now));
	let groups = $derived(groupByShell(profiles));

	function isPinned(profile: RunnerProfile): boolean {
		return profile.selected === true || profile.name === defaultProfile;
	}
	function isRequested(profile: RunnerProfile): boolean {
		return wakeRequest !== null && wakeRequest.profile === profile.name;
	}
	function isSticky(profile: RunnerProfile): boolean {
		return stickyLive !== null && stickyLive.profile === profile.name;
	}
	function isNextWake(profile: RunnerProfile): boolean {
		return wakeRequest ? isRequested(profile) : isPinned(profile);
	}

	/** Who answers the next wake right now, across every profile — the
	 *  shell tab a fresh rack should open on (see `defaultShell`'s own
	 *  doc). Priority matches `isNextWake`'s own: a parked request beats
	 *  the sticky beats the pin. */
	let nextWakeProfile = $derived(
		wakeRequest?.profile ?? stickyLive?.profile ?? defaultProfile ?? null
	);

	// The reader's own tab pick, if any — `$derived`, not an `$effect`
	// re-anchoring `$state`, so the default resolves the same way on the
	// server render as in the browser (an `$effect` never runs during SSR,
	// which left every row unrendered server-side the first way this was
	// written). Falls back to `defaultShell` whenever the reader hasn't
	// chosen, or their choice no longer exists in a fresh report.
	let manualShell = $state<string | null>(null);

	// `focusShell` is only ever set from a browser click on the fuel gauge's
	// provider row (RailGauge → RailBench → here), never present on the
	// first server render, so `$effect`'s SSR gap doesn't apply the way it
	// would to the tab's own default (the comment above `manualShell`
	// explains that constraint; this is a distinct, later-arriving override,
	// not the initial pick). Once the reader taps a different tab manually,
	// `manualShell` moves and this effect stays quiet until the next expand.
	$effect(() => {
		if (focusShell && groups.some((group) => group.shell === focusShell)) {
			manualShell = focusShell;
		}
	});

	let selectedShell = $derived(
		manualShell !== null && groups.some((group) => group.shell === manualShell)
			? manualShell
			: defaultShell(groups, nextWakeProfile) || null
	);
	let activeGroup = $derived(groups.find((group) => group.shell === selectedShell) ?? null);

	/** Which platform the sticky's thread lives on (`telegram`, `slack`) —
	 *  the human-scale half of its correspondent key; never the raw id. */
	function stickyThreadLabel(): string {
		const key = stickyLive?.correspondent_key ?? stickyLive?.conversation_key ?? '';
		const platform = key.split(':').filter(Boolean)[key.startsWith('cloud:') ? 1 : 0];
		return platform ? `${platform} thread` : 'thread';
	}

	/** The core half of the row, distinct from the pin badge's "default":
	 *  this answers "which core", the badge answers "who wakes next" — see
	 *  the module doc for why a row could otherwise print "default" twice
	 *  meaning two different things. Vocabulary matches `runner.py` /
	 *  `run_ledger.py`: "None"/"default" means *unpinned*. */
	function coreLabel(profile: RunnerProfile): string {
		if (profile.model && profile.model !== profile.shell) return profile.model;
		return 'unpinned';
	}

	function handleTap(profile: RunnerProfile) {
		if (!isTappable(profile, stale)) return;
		if (onTap) onTap(profile.name);
	}

	function rowTitle(profile: RunnerProfile): string {
		const offerable = isTappable(profile, stale);
		if (!offerable) return `${profile.name}: ${offReasonOf(profile, stale).text}`;
		if (isRequested(profile)) return 'already requested — tap the default row to cancel';
		if (wakeRequest && isPinned(profile))
			return `back to ${profile.name} — cancels the parked request`;
		return `next wake on ${profile.name} — one wake, cancelable until it fires`;
	}

	function rowClasses(tappable: boolean, requested: boolean, pinned: boolean): string {
		if (!tappable) return OFF_ROW;
		if (requested) return SELECTED_REQUESTED;
		if (pinned) return SELECTED_PINNED;
		return IDLE_ROW;
	}

	// `nextWake` already earns its own left rule from `rowClasses`
	// (SELECTED_REQUESTED/SELECTED_PINNED) — the name text stays off the
	// amber hue too, on the same "selection is a shape, not a colour" call,
	// so the mark isn't restated twice on the same row through two channels.
	function rowLabelClasses(nextWake: boolean, tappable: boolean): string {
		if (nextWake) return 'text-stone-100';
		return tappable ? 'text-stone-300' : 'text-ink-mute';
	}

	// Per-row detail state: rank, quota source, capability score and
	// freshness moved off the row's own line (w-68's bar — "a row shows
	// what you need to choose, not what justified it") into this disclosure.
	let openRows = new SvelteSet<string>();
	function toggleDetail(name: string) {
		if (openRows.has(name)) openRows.delete(name);
		else openRows.add(name);
	}
</script>

<div data-measure="spool-rack" class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<!-- The third workshop bay wears the same label grammar as 01/02 in
		     RailBench (found by this surface's first driven user, 2026-08-19:
		     two numbered stone labels and then an amber unnumbered one read
		     as two different rooms). Amber stays reserved for the DEFAULT
		     badge and selection marks, per the selection-vs-action split. -->
		<span class="font-mono font-bold tracking-[0.14em] uppercase" style="color: rgb(214 211 209)"
			>core</span
		>
		{#if stale}
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				title="the account's newest catalog report is old — no row on this rack can park a wake until a fresh one lands"
				>stale report</span
			>
		{/if}
	</div>
	{#if profiles.length === 0}
		<p class="font-mono text-xs text-ink-quiet">No daemon has reported its catalog yet.</p>
	{:else}
		<!-- Stage one: the shell selector. A small, stable set — never grows
		     with the core count the way the old flat list did. -->
		<div class="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="shell">
			{#each groups as group (group.shell)}
				{@const off = group.allUnavailable}
				{@const active = group.shell === selectedShell}
				<button
					type="button"
					role="tab"
					aria-selected={active}
					title={off ? `${group.shell} — ${deadShellReason(group)}` : group.shell}
					onclick={() => (manualShell = group.shell)}
					class="border px-2.5 py-1 font-mono text-[11px] tracking-wide uppercase transition-colors {off
						? OFF_ROW
						: active
							? 'border-l-2 border-l-stone-100 border-stone-800/60 bg-stone-800/50 text-stone-100'
							: `${IDLE_ROW} text-stone-300`}"
				>
					{off ? OFF_MARK : ''}{group.shell}
				</button>
			{/each}
		</div>

		<!-- Stage two: the selected shell's cores. -->
		{#if activeGroup}
			{#if activeGroup.allUnavailable}
				<p class="mb-2 font-mono text-[10px] text-ink-mute">
					{OFF_MARK}{activeGroup.shell} — {deadShellReason(activeGroup)}
				</p>
			{/if}
			<div class="space-y-1.5">
				{#each activeGroup.profiles as profile (profile.name)}
					{@const pinned = isPinned(profile)}
					{@const requested = isRequested(profile)}
					{@const nextWake = isNextWake(profile)}
					{@const tappable = isTappable(profile, stale)}
					{@const reason = tappable ? null : offReasonOf(profile, stale)}
					{@const open = openRows.has(profile.name)}
					<div
						class="flex w-full flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border {rowClasses(
							tappable,
							requested,
							pinned
						)}"
					>
						<!-- The tap target is this button alone (name/core/class) — the
						     row's own detail-disclosure button below is a *sibling*,
						     never a descendant: two buttons cannot nest without the
						     browser silently repairing the DOM (`node_invalid_placement`),
						     which is exactly the kind of surface bug this redesign
						     exists to stop shipping. -->
						<button
							type="button"
							data-role="rack-row-tap"
							disabled={!tappable}
							onclick={() => handleTap(profile)}
							title={rowTitle(profile)}
							class="flex min-w-0 items-baseline gap-3 px-2 py-1.5 text-left"
						>
							<span
								class="font-mono text-xs font-medium tracking-wide {rowLabelClasses(
									nextWake,
									tappable
								)}">{tappable ? '' : OFF_MARK}{profile.name}</span
							>
							<span class="font-mono text-[11px] text-ink-quiet">{coreLabel(profile)}</span>
							{#if profile.class}
								<span class="font-mono text-[10px] tracking-wide text-stone-400 uppercase"
									>{profile.class}</span
								>
							{/if}
						</button>
						<div class="flex items-baseline gap-3 px-2 py-1.5 font-mono text-[11px]">
							{#if isSticky(profile)}
								<!-- #932: the claimed tap riding its conversation — a live state,
									     not a selection, so it wears its own recipe rather than the
									     badges' shape language (w-68's bar). -->
								<span
									class="flex items-baseline gap-1.5 border {LIVE_CLAIM} px-1.5 py-0.5 text-[10px] tracking-wide uppercase"
									title={`a tapped core rides its conversation ${stickyLive?.persistent ? 'until changed or released' : 'until the timer runs out'} — wakes in that thread dispatch here, not on the default${stickyLive?.expires_at && !stickyLive.persistent ? ` (until ${stickyLive.expires_at})` : ''}`}
								>
									<span
										class="inline-block h-1.5 w-1.5 rounded-full bg-stone-200"
										aria-hidden="true"
									></span>
									riding {stickyThreadLabel()}
									{#if stickyCountdown(stickyLive, now)}
										· {stickyCountdown(stickyLive, now)}
									{/if}
									{#if onReleaseSticky}
										<span
											role="button"
											tabindex="0"
											title="release now — this thread's wakes go back to the default"
											class="cursor-pointer px-0.5 text-stone-300 hover:text-stone-100"
											onclick={(e) => {
												e.stopPropagation();
												onReleaseSticky?.();
											}}
											onkeydown={(e) => {
												if (e.key === 'Enter' || e.key === ' ') {
													e.stopPropagation();
													e.preventDefault();
													onReleaseSticky?.();
												}
											}}>✕</span
										>
									{/if}
								</span>
							{/if}
							{#if requested}
								<span
									class="border border-l-2 border-l-stone-100 border-stone-800/60 bg-stone-800/50 px-1.5 py-0.5 text-[10px] tracking-wide text-stone-100 uppercase"
									>next wake · requested</span
								>
							{:else if pinned}
								<span
									class="border border-l-2 px-1.5 py-0.5 text-[10px] tracking-wide uppercase {nextWake
										? 'border-l-stone-100 border-stone-800/60 bg-stone-800/50 text-stone-100'
										: 'border-l-stone-500 border-stone-900/40 bg-stone-900/30 text-stone-400'}"
									>default</span
								>
							{:else if !tappable}
								<span class="text-ink-mute normal-case">{reason?.text}</span>
							{/if}
							{#if profile.class || profile.cost_rank !== null || profile.quota_source || profile.capability_score !== null}
								<button
									type="button"
									onclick={(e) => {
										e.stopPropagation();
										toggleDetail(profile.name);
									}}
									title={open ? 'hide detail' : 'why this row — rank, quota source, capability'}
									class="text-ink-quiet hover:text-ink-mute">{open ? '▾' : '▸'} detail</button
								>
							{/if}
						</div>
						{#if open}
							<div
								class="flex flex-wrap items-baseline gap-3 border-t border-stone-800/60 px-2 py-1.5 font-mono text-[10px] text-ink-quiet"
							>
								{#if profile.cost_rank !== null && profile.cost_rank !== undefined}
									<span>rank {profile.cost_rank}</span>
								{/if}
								{#if profile.quota_source}
									<span class="text-ink-mute">{profile.quota_source}</span>
								{/if}
								{#if profile.capability_score !== null && profile.capability_score !== undefined}
									<span
										title={profile.capability_freshness
											? `benchmark as of ${profile.capability_freshness}`
											: undefined}>cap {profile.capability_score}</span
									>
								{/if}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	{/if}
</div>
