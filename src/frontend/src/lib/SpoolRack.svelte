<script lang="ts">
	import type { RunnerProfile } from './runners';

	// #328 spool rack — read-only by design. You don't set a being's body
	// with a dropdown; the rack shows who *can* wake and which spool is
	// threaded (the pin). Changing it is a conversation: tell the resident,
	// it parks a config-change request, you confirm on the approve page —
	// the same loop every other setting already uses. No selector here, on
	// purpose.
	interface Props {
		profiles: RunnerProfile[];
		defaultProfile: string | null;
		stale: boolean;
	}

	let { profiles, defaultProfile, stale }: Props = $props();

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
		<p class="font-mono text-xs text-stone-500">No daemon has reported its catalog yet.</p>
	{:else}
		<div class="space-y-1.5">
			{#each profiles as profile (profile.name)}
				{@const pinned = isPinned(profile)}
				<div
					class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border px-2 py-1.5 {pinned
						? 'border-amber-800/70 bg-amber-950/20'
						: 'border-stone-800/60 bg-stone-900/30'}"
				>
					<div class="flex items-baseline gap-3">
						<span
							class="font-mono text-xs font-medium tracking-wide {pinned
								? 'text-amber-200'
								: 'text-stone-300'}">{profile.name}</span
						>
						<span class="font-mono text-[11px] text-stone-500"
							>{profile.shell ?? '?'} · {coreLabel(profile)}</span
						>
					</div>
					<div class="flex items-baseline gap-3 font-mono text-[11px]">
						{#if pinned}
							<!-- The threaded shuttle: who wakes next unless addressed
							     otherwise. Label + border, never color alone. -->
							<span
								class="border border-amber-700/70 bg-amber-950/40 px-1.5 py-0.5 text-[10px] tracking-wide text-amber-300 uppercase"
								>next wake</span
							>
						{/if}
						{#if profile.class}
							<span class="tracking-wide text-stone-400 uppercase"
								>{CLASS_LABEL[profile.class] ?? profile.class}</span
							>
						{/if}
						{#if profile.cost_rank !== null && profile.cost_rank !== undefined}
							<span class="text-stone-500">rank {profile.cost_rank}</span>
						{/if}
						{#if profile.quota_source}
							<span class="text-stone-600">{profile.quota_source}</span>
						{/if}
						{#if profile.capability_score !== null && profile.capability_score !== undefined}
							<span
								class="text-stone-500"
								title={profile.capability_freshness
									? `benchmark as of ${profile.capability_freshness}`
									: undefined}>cap {profile.capability_score}</span
							>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
