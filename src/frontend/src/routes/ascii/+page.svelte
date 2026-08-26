<script lang="ts">
	// /ascii — the reference camera over the RoomGraph
	// (design-room-operational-topology.md §ASCII reference renderer).
	//
	// The page owns nothing semantic: it polls the two existing wires,
	// compiles the RoomGraph ($lib/roomGraph), and prints the board
	// ($lib/asciiRoom) into a <pre>. If this view can't tell the story, the
	// *model* is what's underdefined — that is the point of serving it while
	// the pretty renderers are still in flight.
	//
	// Motion doctrine, ASCII edition: nothing animates. A line that changed
	// between two polls flashes once (a client diff between two attested
	// snapshots — the same rule the resident field follows); everything else
	// holds still. No ambient life, no interpolation.
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { fetchLiveRuns, LiveRunsAuthError, type LiveRunsResponse } from '$lib/liveRuns';
	import { fetchRunLedger, type RunLedgerResponse } from '$lib/runLedger';
	import { compileRoomGraph, type TrailStep } from '$lib/roomGraph';
	import { renderRoomGraph, LEGEND } from '$lib/asciiRoom';
	import { demoFrames } from '../new/demo';

	const POLL_MS = 2000;
	const LEDGER_MS = 60_000;
	const DEMO_STEP_MS = 3600;
	const WIDTH = 76;

	let lines = $state<string[]>([]);
	let changed = $state<number[]>([]);
	let loading = $state(true);
	let signedOut = $state(false);
	let stale = $state(false);
	let demo = $state(false);
	let frameNote = $state('');
	let showLegend = $state(true);

	let live: LiveRunsResponse | null = null;
	let ledger: RunLedgerResponse | null = null;

	// The island's terrain memory: attested footsteps per run, deduped by
	// boundary timestamp — "only what you touch comes into being". Session-
	// local on purpose: durable exploration memory is the doc's gap #7.
	const trails: Record<string, TrailStep[]> = {};
	const TRAIL_CAP = 60;

	function recordTrails() {
		for (const run of live?.runs ?? []) {
			const dir = run.edge?.dir && run.edge.dir !== '.' ? run.edge.dir : null;
			const at = run.edge?.at ?? null;
			if (!dir || !at) continue;
			const trail = (trails[run.run_id] ??= []);
			if (trail.some((s) => s.at === at)) continue;
			trail.push({ dir, act: run.edge?.act ?? null, at });
			if (trail.length > TRAIL_CAP) trail.splice(0, trail.length - TRAIL_CAP);
		}
	}

	// The flash marks *state* motion only. Elapsed-time labels churn every
	// poll, so the diff runs on a clock-free render (`now` omitted drops
	// every elapsed label) while the display keeps its clocks — a line
	// flashes when the world moved, never because a minute passed.
	let prevBare: string[] = [];

	function repaint(now: number) {
		recordTrails();
		const graph = compileRoomGraph(live, ledger, trails);
		stale = graph.stale;
		const next = renderRoomGraph(graph, { width: WIDTH, now }).split('\n');
		const bare = renderRoomGraph(graph, { width: WIDTH }).split('\n');
		const delta: number[] = [];
		for (let i = 0; i < bare.length; i++) {
			if (prevBare.length > 0 && bare[i] !== prevBare[i]) delta.push(i);
		}
		prevBare = bare;
		lines = next;
		changed = delta;
	}

	onMount(() => {
		demo = new URLSearchParams(location.search).has('demo');
		let stop = false;
		let timer: ReturnType<typeof setTimeout> | null = null;

		if (demo) {
			// The same replay `/new?demo` steps through — one fixture, two
			// cameras, same story. The ledger is a tiny synthetic tail so the
			// Cloth section has a past to stand on.
			const frames = demoFrames();
			ledger = {
				generated_at: '2026-08-26T10:00:00Z',
				rows: [
					{
						run_id: 'run-260826-0830-jknr',
						event_id: null,
						started_at: '2026-08-26T08:30:00Z',
						ended_at: '2026-08-26T08:52:00Z',
						wall_clock_seconds: 1320,
						runner_shell: 'claude',
						runner_core: 'sonnet',
						core_expected: null,
						core_mismatch: null,
						substitution_reason: null,
						repo_label: 'hugimuni-labs/brnrd',
						source_system: 'schedule',
						name: 'the-overture-pickup',
						external_refs: [
							{ kind: 'commit', sha: 'ab12cd3', subject: 'fix: connector gap' },
							{ kind: 'pr', number: 1634 }
						],
						parent_run_id: null,
						is_subspawn: false,
						tokens_input: null,
						tokens_output: null,
						tokens_cache_read: null,
						tokens_cache_creation: null,
						context_window_used: null,
						weekly_pct_delta: null,
						five_hour_pct_delta: null,
						usd_subscription_attributed: 1.12,
						usd_credits_equivalent: null,
						estimate_vs_actual: null
					}
				],
				stale: false,
				reported_at: '2026-08-26T10:00:00Z',
				span_seconds_served: 86400
			} as RunLedgerResponse;
			let i = 0;
			const step = () => {
				if (stop) return;
				live = {
					generated_at: '2026-08-26T11:00:00Z',
					runs: frames[i % frames.length],
					stale: false,
					reported_at: '2026-08-26T11:00:00Z',
					spawn_max_concurrent: 3
				};
				frameNote = `replay ${(i % frames.length) + 1}/${frames.length}`;
				repaint(Date.parse('2026-08-26T11:20:00Z'));
				loading = false;
				i += 1;
				timer = setTimeout(step, DEMO_STEP_MS);
			};
			step();
			return () => {
				stop = true;
				if (timer) clearTimeout(timer);
			};
		}

		let lastLedgerAt = 0;
		const poll = async () => {
			if (stop) return;
			try {
				const nowMs = Date.now();
				if (nowMs - lastLedgerAt > LEDGER_MS) {
					lastLedgerAt = nowMs;
					try {
						ledger = await fetchRunLedger(fetch, 8);
					} catch {
						// ledger is enrichment; the board stands without it
					}
				}
				live = await fetchLiveRuns();
				signedOut = false;
				repaint(nowMs);
			} catch (err) {
				if (err instanceof LiveRunsAuthError) signedOut = true;
			} finally {
				loading = false;
			}
			if (!stop) timer = setTimeout(poll, POLL_MS);
		};
		poll();
		return () => {
			stop = true;
			if (timer) clearTimeout(timer);
		};
	});
</script>

<svelte:head>
	<title>brnrd · the room, in characters</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<div class="deck">
	<header>
		<span class="mark">b·_·d</span>
		<span class="title">the room, in characters</span>
		<span class="status">
			{#if loading}connecting…{:else if signedOut}signed out — <a href={resolve('/')}>sign in</a
				>{:else if demo}{frameNote}{:else if stale}wire stale{:else}live{/if}
		</span>
	</header>

	{#if !signedOut}
		<pre class="board">{#each lines as line, i (i)}<span
					class="line"
					class:delta={changed.includes(i)}>{line + '\n'}</span
				>{/each}</pre>
	{/if}

	<button class="legend-toggle" onclick={() => (showLegend = !showLegend)}>
		{showLegend ? 'hide legend' : 'legend'}
	</button>
	{#if showLegend}
		<pre class="legend">{LEGEND}</pre>
	{/if}
</div>

<style>
	:global(body) {
		background: #0b0f0c;
	}
	.deck {
		min-height: 100vh;
		padding: 1rem;
		font-family: 'SFMono-Regular', ui-monospace, Menlo, monospace;
		color: #9be9a8;
	}
	header {
		display: flex;
		gap: 0.75rem;
		align-items: baseline;
		margin-bottom: 0.75rem;
	}
	.mark {
		color: #e8c15a;
		font-weight: 600;
	}
	.title {
		color: #6ea87a;
	}
	.status {
		margin-left: auto;
		color: #587a61;
		font-size: 0.85rem;
	}
	.status a {
		color: #9be9a8;
	}
	.board {
		margin: 0;
		font-size: clamp(9px, 1.9vw, 13px);
		line-height: 1.35;
		overflow-x: auto;
		white-space: pre;
	}
	.line {
		display: inline;
	}
	.line.delta {
		animation: settle 1.4s ease-out 1;
	}
	@keyframes settle {
		0% {
			background: rgba(232, 193, 90, 0.35);
			color: #ffe9b0;
		}
		100% {
			background: transparent;
		}
	}
	.legend-toggle {
		margin-top: 1rem;
		background: none;
		border: 1px solid #2a4030;
		color: #587a61;
		font: inherit;
		font-size: 0.8rem;
		padding: 0.2rem 0.6rem;
		cursor: pointer;
	}
	.legend {
		margin-top: 0.5rem;
		color: #587a61;
		font-size: 0.8rem;
	}
</style>
