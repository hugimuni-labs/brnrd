<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { ageSince, type LiveRun } from './liveRuns';
	import { STATUS_GOOD, STATUS_WARN, STATUS_UNKNOWN } from './statusPalette';

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
	// fabricated one. Same palette module as WindowTrack (`statusPalette.ts`)
	// — an import, not a retyped hex, so the two can't drift the way this
	// file and WindowTrack's did before 2026-07-08 (this file's old
	// `#0ca30c`/`#fab219` sat under a comment claiming that parity while
	// WindowTrack had already moved to hearth/frost/void): running = good
	// (amber), stalling = warn (frost), unknown recedes. A status color
	// never doubles as a series identity.
	const LEVEL_COLOR: Record<'running' | 'stalling' | 'unknown', string> = {
		running: STATUS_GOOD,
		stalling: STATUS_WARN,
		unknown: STATUS_UNKNOWN
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

	// #200's remaining slice: the heartbeat-freshness label above only ever
	// said "running" while a thought worked — real lifecycle phase
	// (queued/preparing/finalizing/delivering/attending/...) was invisible.
	// A live, non-stalling row now prefers the real phase reported by
	// `run_progress.project_run` (`cloud.py::_live_runs_snapshot`); a
	// stalling/unknown/phaseless row keeps the generic freshness label —
	// "stalling" is more informative than a stale phase reading.
	function label(run: LiveRun, lvl: 'running' | 'stalling' | 'unknown'): string {
		if (lvl === 'running' && run.phase) return run.phase;
		return LEVEL_LABEL[lvl];
	}

	// Multi-workstream slice 1 (kb/design-multi-workstream-concurrency.md
	// "Ranked moves" #1): `spawn:`'s pool grew past a cap of 1, so this grid
	// can now hold several concurrent worker-stack children alongside the
	// resident thought that dispatched them. "Flatten the view, not the
	// write-authority" was the recommendation, not a full parent/child tree
	// — every run still gets an equal peer card, in the same chronological
	// order as before. The one addition is the "↳ spawn" tag below: a
	// same-weight visual cue for "this card is a dispatched child", with the
	// parent's own label (if it's still live in this same snapshot) on
	// hover, so a page full of peer cards doesn't read as an unexplained
	// jump from N-1 to N runs when a spawn lands.
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">live runs</span>
		{#if stale}
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
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
				{@const parentLabel = run.parent_run_id
					? runs.find((r) => r.run_id === run.parent_run_id)?.label
					: null}
				<div
					class="subpanel p-2.5 text-xs"
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
							<span
								class="truncate font-mono font-medium tracking-wide uppercase"
								style={`color: ${color}`}
							>
								{label(run, lvl)}
							</span>
						</span>
						<span class="shrink-0 font-mono text-stone-500"
							>{ageSince(run.started_at, now) ?? ''}</span
						>
					</div>
					<p class="mt-1.5 flex min-w-0 items-center gap-1.5">
						<span class="truncate font-medium text-amber-100">{primary}</span>
						{#if run.is_subspawn}
							<span
								class="shrink-0 border border-amber-900/60 bg-amber-950/40 px-1 py-0.5 font-mono text-[9px] tracking-wide text-amber-300 uppercase"
								title={parentLabel ? `spawned by ${parentLabel}` : 'spawned child'}>↳ spawn</span
							>
						{/if}
					</p>
					<p class="truncate text-stone-500">{secondary}</p>
					{#if run.card_text}
						<!-- Progress-card note (`.card`, `run_progress.py`'s
						     `agent_card_text`) — the same narration a chat surface
						     already renders mid-run, now visible on the dashboard
						     row too (#200's remaining slice). Truncated to one line;
						     the full note rides in `title` for a hover. -->
						<p class="mt-0.5 truncate text-stone-400 italic" title={run.card_text}>
							{run.card_text}
						</p>
					{/if}
					<!-- No known total duration to bind a real percent to, so a
					     running card gets an indeterminate scanning bar (the
					     Zachtronics "in motion" tell) instead of a fabricated fill.
					     A stalling/unknown card freezes it — but freezing the same
					     w-1/3 segment in place (2026-07-08 first cut) read as a stuck
					     33%-done fill, not "no longer moving": live-caught same day
					     ("reads more as a quarter filled bar, like it is still
					     running"). Fixed by widening the frozen state to the full
					     track at low opacity — a flatlined signal has no fill amount
					     to misread, where a frozen fraction always looks like one. -->
					<div class="mt-2 h-1 overflow-hidden bg-stone-900" aria-hidden="true">
						<div
							class={`h-full ${lvl === 'running' ? 'w-1/3 animate-[loom-scan_1.4s_ease-in-out_infinite]' : 'w-full'}`}
							style={`background-color: ${color}; opacity: ${lvl === 'running' ? 1 : 0.3}`}
						></div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
