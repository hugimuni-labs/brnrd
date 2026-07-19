<script lang="ts">
	import type { RunnerProfile, WakeRequest } from './runners';

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
	interface Props {
		profiles: RunnerProfile[];
		defaultProfile: string | null;
		stale: boolean;
		wakeRequest: WakeRequest | null;
		onTap?: (profileName: string) => void;
	}

	let { profiles, defaultProfile, stale, wakeRequest, onTap }: Props = $props();

	const CLASS_LABEL: Record<string, string> = {
		economy: 'economy',
		balanced: 'balanced',
		strong: 'strong'
	};

	function coreLabel(profile: RunnerProfile): string {
		if (profile.model && profile.model !== profile.shell) return profile.model;
		return 'default';
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
		if (onTap) onTap(profile.name);
	}

	function rowTitle(profile: RunnerProfile): string {
		if (isRequested(profile)) {
			return 'already requested — tap the default row to cancel';
		}
		if (wakeRequest && isPinned(profile)) {
			return `back to ${profile.name} — cancels the parked request`;
		}
		return `next wake on ${profile.name} — one wake, cancelable until it fires`;
	}
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">spool rack</span>
		{#if stale}
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		{/if}
	</div>
	{#if profiles.length === 0}
		<p class="font-mono text-xs text-ink-quiet">No daemon has reported its catalog yet.</p>
	{:else}
		<div class="space-y-1.5">
			{#each profiles as profile (profile.name)}
				{@const pinned = isPinned(profile)}
				{@const requested = isRequested(profile)}
				{@const nextWake = isNextWake(profile)}
				<button
					type="button"
					onclick={() => handleTap(profile)}
					title={rowTitle(profile)}
					class="flex w-full flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border px-2 py-1.5 text-left transition-colors {requested
						? 'border-amber-600/80 bg-amber-950/40'
						: pinned
							? 'border-amber-800/70 bg-amber-950/20'
							: 'border-stone-800/60 bg-stone-900/30 hover:border-stone-600/70'}"
				>
					<div class="flex items-baseline gap-3">
						<span
							class="font-mono text-xs font-medium tracking-wide {nextWake
								? 'text-amber-200'
								: 'text-stone-300'}">{profile.name}</span
						>
						<span class="font-mono text-[11px] text-ink-quiet"
							>{profile.shell ?? '?'} · {coreLabel(profile)}</span
						>
					</div>
					<div class="flex items-baseline gap-3 font-mono text-[11px]">
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
							<span class="tracking-wide text-stone-400 uppercase"
								>{CLASS_LABEL[profile.class] ?? profile.class}</span
							>
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
	{/if}
</div>
