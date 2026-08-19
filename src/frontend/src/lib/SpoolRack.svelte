<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import { liveSticky, type RunnerProfile, type RunnerSticky, type WakeRequest } from './runners';
	import { stickyCountdown } from './controlStrip';
	import { availabilityOf, collapsedShellSummary, groupByShell, isTappable } from './spoolRack';
	import {
		DISABLED_ROW,
		IDLE_ROW,
		SELECTED_PINNED,
		SELECTED_REQUESTED,
		UNAVAILABLE_MARK
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
	// 2026-08-19 rework ("the rack of dead spools"): rows used to render
	// `available` on any row *missing* the field, and a stale report's rows
	// stayed tappable because the stale chip was cosmetic — a dead machine's
	// catalog could park a wake nothing would ever serve. Availability is
	// tri-state now (`spoolRack.ts::availabilityOf`) and tap-gating reads
	// staleness too (`isTappable`). The rows themselves are grouped by
	// shell — the "two-way selector" shape (shell, then its cores) a
	// dropdown would have meant re-litigating the no-selector call above;
	// grouping is presentation, tap semantics are untouched.
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
	}

	let {
		profiles,
		defaultProfile,
		stale,
		wakeRequest,
		sticky = null,
		now = Date.now(),
		onTap,
		onReleaseSticky
	}: Props = $props();

	let stickyLive = $derived(liveSticky(sticky, now));
	let groups = $derived(groupByShell(profiles));
	// Which collapsed (all-unavailable) shells the reader expanded by hand —
	// still never tappable-to-request once open, only the dead rows made
	// inspectable instead of hidden.
	let expandedShells = new SvelteSet<string>();

	function toggleShell(shell: string) {
		if (expandedShells.has(shell)) expandedShells.delete(shell);
		else expandedShells.add(shell);
	}

	function isSticky(profile: RunnerProfile): boolean {
		return stickyLive !== null && stickyLive.profile === profile.name;
	}

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

	function isPinned(profile: RunnerProfile): boolean {
		return profile.selected === true || profile.name === defaultProfile;
	}

	function isRequested(profile: RunnerProfile): boolean {
		return wakeRequest !== null && wakeRequest.profile === profile.name;
	}

	/** Who actually answers the next wake: the tap when one is parked,
	 *  the pin otherwise. This still drives emphasis, while the badges keep
	 *  standing default and one-shot request as visibly different concepts. */
	function isNextWake(profile: RunnerProfile): boolean {
		return wakeRequest ? isRequested(profile) : isPinned(profile);
	}

	function handleTap(profile: RunnerProfile) {
		if (!isTappable(profile, stale)) return;
		if (onTap) onTap(profile.name);
	}

	function rowTitle(profile: RunnerProfile): string {
		const availability = availabilityOf(profile);
		if (availability === 'unverified') {
			return `${profile.name}: this daemon's report didn't say whether it's available — not tappable until it does`;
		}
		if (availability === 'unavailable') {
			return profile.availability === 'shell-not-found'
				? `${profile.shell ?? profile.name} is not installed on this daemon`
				: `${profile.name} is unavailable: ${profile.availability ?? 'daemon policy'}`;
		}
		if (stale || profile.daemon_stale === true) {
			return `${profile.name} was available as of an outdated report from its own daemon — not tappable until a fresher one lands`;
		}
		if (isRequested(profile)) {
			return 'already requested — tap the default row to cancel';
		}
		if (wakeRequest && isPinned(profile)) {
			return `back to ${profile.name} — cancels the parked request`;
		}
		return `next wake on ${profile.name} — one wake, cancelable until it fires`;
	}

	function rowClasses(
		profile: RunnerProfile,
		tappable: boolean,
		requested: boolean,
		pinned: boolean
	): string {
		if (!tappable) {
			const availability = availabilityOf(profile);
			return availability === 'unverified'
				? 'cursor-not-allowed border-dashed border-stone-800/60 bg-stone-950/20 opacity-60'
				: DISABLED_ROW;
		}
		if (requested) return SELECTED_REQUESTED;
		if (pinned) return SELECTED_PINNED;
		return IDLE_ROW;
	}

	function rowLabelClasses(nextWake: boolean, tappable: boolean, unverified: boolean): string {
		if (nextWake) return 'text-amber-200';
		if (unverified) return 'text-ink-quiet';
		return tappable ? 'text-stone-300' : 'text-ink-mute';
	}

	function rowMark(availability: ReturnType<typeof availabilityOf>): string {
		if (availability === 'unavailable') return UNAVAILABLE_MARK;
		if (availability === 'unverified') return '? ';
		return '';
	}
</script>

<div data-measure="spool-rack" class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">spool rack</span>
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
		<div class="space-y-2.5">
			{#each groups as group (group.shell)}
				{@const collapsed = group.allUnavailable && !expandedShells.has(group.shell)}
				{#if collapsed}
					<!-- A shell with nothing installed collapses to one line instead
					     of one dead row per core — the screenshot's 7 greyed rows,
					     made impossible. Chevron only inspects; it never taps. -->
					<button
						type="button"
						onclick={() => toggleShell(group.shell)}
						title="expand to see this shell's cores — still not tappable, nothing here is installed"
						class="flex w-full items-center justify-between gap-3 border border-stone-900/60 bg-stone-950/20 px-2 py-1.5 text-left font-mono text-xs text-ink-mute transition-colors hover:border-stone-700/60"
					>
						<span>{UNAVAILABLE_MARK}{collapsedShellSummary(group)}</span>
						<span class="text-[10px] text-ink-quiet">▸</span>
					</button>
				{:else}
					<div class="space-y-1">
						<div class="flex items-center justify-between px-0.5">
							<span class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase"
								>{group.shell}</span
							>
							{#if group.allUnavailable}
								<button
									type="button"
									onclick={() => toggleShell(group.shell)}
									title="collapse — nothing in this shell is installed"
									class="font-mono text-[10px] text-ink-mute hover:text-ink-quiet">▾</button
								>
							{/if}
						</div>
						<div class="space-y-1.5">
							{#each group.profiles as profile (profile.name)}
								{@const pinned = isPinned(profile)}
								{@const requested = isRequested(profile)}
								{@const nextWake = isNextWake(profile)}
								{@const availability = availabilityOf(profile)}
								{@const tappable = isTappable(profile, stale)}
								<button
									type="button"
									disabled={!tappable}
									onclick={() => handleTap(profile)}
									title={rowTitle(profile)}
									class="flex w-full flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border px-2 py-1.5 text-left transition-colors {rowClasses(
										profile,
										tappable,
										requested,
										pinned
									)}"
								>
									<div class="flex items-baseline gap-3">
										<span
											class="font-mono text-xs font-medium tracking-wide {rowLabelClasses(
												nextWake,
												tappable,
												availability === 'unverified'
											)}">{rowMark(availability)}{profile.name}</span
										>
										<span class="font-mono text-[11px] text-ink-quiet">{coreLabel(profile)}</span>
										{#if availability === 'available' && !tappable}
											<span class="font-mono text-[10px] tracking-wide text-sky-400 uppercase"
												>stale</span
											>
										{/if}
									</div>
									<div class="flex items-baseline gap-3 font-mono text-[11px]">
										{#if isSticky(profile)}
											<!-- #932: the claimed tap riding its conversation. Timer is the
											     contract made visible (his 08-08 ask); ✕ is the early exit. -->
											<span
												class="flex items-baseline gap-1.5 border border-amber-600/80 bg-amber-950/60 px-1.5 py-0.5 text-[10px] tracking-wide text-amber-200 uppercase"
												title={`a tapped core rides its conversation until the timer runs out — wakes in that thread dispatch here, not on the default${stickyLive?.expires_at ? ` (until ${stickyLive.expires_at})` : ''}`}
											>
												riding {stickyThreadLabel()}
												{#if stickyCountdown(stickyLive, now)}
													· {stickyCountdown(stickyLive, now)}
												{/if}
												{#if onReleaseSticky}
													<span
														role="button"
														tabindex="0"
														title="release now — this thread's wakes go back to the default"
														class="cursor-pointer px-0.5 text-amber-300 hover:text-amber-100"
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
											<!-- The parked tap: one wake, then back to the pin.
											     Cancel = tap the default row, not this one. -->
											<span
												class="border border-amber-600/80 bg-amber-950/60 px-1.5 py-0.5 text-[10px] tracking-wide text-amber-200 uppercase"
												>next wake · requested</span
											>
										{:else if pinned}
											<!-- The standing pin is never a one-shot request. It may be
											     active or temporarily superseded, but its name stays DEFAULT
											     so the rack cannot recreate the ambiguity the header fixes. -->
											<span
												class="border px-1.5 py-0.5 text-[10px] tracking-wide uppercase {nextWake
													? 'border-amber-700/70 bg-amber-950/40 text-amber-300'
													: 'border-sky-800/70 bg-sky-950/40 text-sky-300'}">default</span
											>
										{/if}
										{#if profile.class}
											<span class="tracking-wide text-stone-400 uppercase">{profile.class}</span>
										{/if}
										{#if profile.cost_rank !== null && profile.cost_rank !== undefined}
											<span class="text-ink-quiet">rank {profile.cost_rank}</span>
										{/if}
										{#if profile.quota_source}
											<span class="text-ink-mute">{profile.quota_source}</span>
										{/if}
										{#if profile.capability_score !== null && profile.capability_score !== undefined}
											<span
												class="text-ink-quiet"
												title={profile.capability_freshness
													? `benchmark as of ${profile.capability_freshness}`
													: undefined}>cap {profile.capability_score}</span
											>
										{/if}
									</div>
								</button>
							{/each}
						</div>
					</div>
				{/if}
			{/each}
		</div>
	{/if}
</div>
