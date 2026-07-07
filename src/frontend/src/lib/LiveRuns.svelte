<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { ageSince, type LiveRun } from './liveRuns';

	interface Props {
		runs: LiveRun[];
		stale: boolean;
		now: number;
	}

	let { runs, stale, now }: Props = $props();

	// Loom slice 2 (kb/plan-loom-realtime-build.md #270): live-runs as
	// SpaceChem-molecule cards, not a plain list. The issue named
	// "queued/running/done positions" — checked against the real data
	// before building: the presence registry (`src/brr/presence.py`) only
	// ever holds *active* entries (registered on run start, deregistered on
	// finish), so there's no queued or done state to render, only
	// running-or-gone. "Done" already reads as the existing fade-out exit
	// transition below; "queued" isn't representable without a new backend
	// collector, which the plan deliberately deferred to keep this slice at
	// zero new backend data. What the data *does* carry that the old plain
	// list never used: `last_seen`, freshness of the last heartbeat — a
	// real second state (running vs. stalling-toward-prune), not a
	// fabricated one. Same three-tier palette as WindowTrack (dataviz
	// skill's palette.md): a status color never doubles as a series
	// identity.
	const LEVEL_COLOR: Record<'running' | 'stalling' | 'unknown', string> = {
		running: '#0ca30c',
		stalling: '#fab219',
		unknown: '#57534e'
	};
	const LEVEL_LABEL: Record<'running' | 'stalling' | 'unknown', string> = {
		running: 'running',
		stalling: 'stalling',
		unknown: 'unknown'
	};

	// A heartbeat lands roughly every 30s (`daemon.py`'s watch loop); three
	// missed beats reads as genuinely stalling rather than a single slow
	// tick. The registry itself only prunes at 300s
	// (`presence.DEFAULT_STALE_AFTER_S`), so a card can sit in "stalling"
	// for a while before it's gone — that gap is real and worth seeing.
	const STALL_AFTER_MS = 90_000;

	function level(lastSeen: string | null): 'running' | 'stalling' | 'unknown' {
		if (stale) return 'unknown';
		const seen = lastSeen ? Date.parse(lastSeen) : NaN;
		if (Number.isNaN(seen)) return 'unknown';
		return now - seen > STALL_AFTER_MS ? 'stalling' : 'running';
	}
</script>

<div class="rounded-md border border-stone-800 bg-stone-900/60 p-3">
	<div class="mb-2 flex items-center justify-between text-sm">
		<span class="font-medium text-amber-200">live runs</span>
		{#if stale}
			<span class="rounded bg-sky-900/40 px-1.5 py-0.5 text-xs text-sky-300">stale report</span>
		{/if}
	</div>
	{#if runs.length === 0}
		<p class="text-sm text-stone-500">Nothing awake right now.</p>
	{:else}
		<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
			{#each runs as run (run.id)}
				{@const primary = run.label || run.kind || 'run'}
				{@const secondary = run.label
					? `${run.repo_label || 'unknown repo'} · ${run.kind || 'run'}`
					: run.repo_label || 'unknown repo'}
				{@const lvl = level(run.last_seen)}
				{@const color = LEVEL_COLOR[lvl]}
				<div
					class="rounded-lg border border-stone-800 bg-stone-800/60 p-2.5 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-center justify-between gap-2">
						<span class="flex min-w-0 items-center gap-1.5">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full"
								style={`background-color: ${color}`}
								aria-hidden="true"
							></span>
							<span class="truncate font-medium tracking-wide uppercase" style={`color: ${color}`}>
								{LEVEL_LABEL[lvl]}
							</span>
						</span>
						<span class="shrink-0 text-stone-500">{ageSince(run.started_at, now) ?? ''}</span>
					</div>
					<p class="mt-1.5 truncate font-medium text-amber-100">{primary}</p>
					<p class="truncate text-stone-500">{secondary}</p>
					<!-- No known total duration to bind a real percent to, so a
					     running card gets an indeterminate scanning bar (the
					     Zachtronics "in motion" tell) instead of a fabricated fill;
					     a stalling/unknown card freezes it rather than claim activity
					     the freshness data can no longer back up. -->
					<div class="mt-2 h-1 overflow-hidden rounded-full bg-stone-900" aria-hidden="true">
						<div
							class={`h-full w-1/3 rounded-full ${lvl === 'running' ? 'animate-[loom-scan_1.4s_ease-in-out_infinite]' : ''}`}
							style={`background-color: ${color}; opacity: ${lvl === 'running' ? 1 : 0.5}`}
						></div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
