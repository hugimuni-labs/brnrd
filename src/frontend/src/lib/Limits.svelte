<script lang="ts">
	import { quotaLevel } from './quota';
	import {
		STATUS_GOOD,
		STATUS_WARN,
		STATUS_CRITICAL,
		STATUS_UNKNOWN,
		statusDotStyle,
		statusBarStyle
	} from './statusPalette';

	// Loom envelope Phase 1 (`kb/design-multi-workstream-concurrency.md`
	// §"Loom envelope") — a small panel listing today's real user-tunable
	// ceilings as a pressure meter, reusing WindowTrack's exact dot/bar
	// vocabulary rather than inventing a new visual language for "here's
	// what you've allowed" (a genuinely different axis from "here's what's
	// happening", per that page's own reasoning against retrofitting
	// LiveRuns/WindowTrack directly). Phase 1 has exactly one tunable
	// ceiling today (`spawn.max_concurrent`); the panel is written to grow
	// a row at a time as more configured limits appear (pacing floors, a
	// future budget cap), not to be redesigned per limit.

	interface Props {
		activeSpawns: number;
		maxSpawns: number | null;
	}

	let { activeSpawns, maxSpawns }: Props = $props();

	const LEVEL_COLOR: Record<string, string> = {
		ample: STATUS_GOOD,
		low: STATUS_WARN,
		critical: STATUS_CRITICAL,
		unknown: STATUS_UNKNOWN
	};

	// The track drains toward the limit, same convention WindowTrack's own
	// comment states: this bar's fill is *headroom* (slots still free), not
	// slots in use, so "ample" reads as green-equivalent (plenty of room)
	// and "critical" reads as the pool actually at/near capacity — the same
	// direction a quota window drains as it's spent.
	let headroomPct = $derived(
		maxSpawns && maxSpawns > 0 ? Math.max(0, ((maxSpawns - activeSpawns) / maxSpawns) * 100) : null
	);
	let level = $derived(quotaLevel(headroomPct));
	let color = $derived(LEVEL_COLOR[level]);
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">limits</span>
	</div>
	<div>
		<div class="mb-1 flex items-baseline justify-between font-mono text-xs text-stone-400">
			<span class="tracking-wide uppercase">spawn slots</span>
			<span class="flex items-center gap-1.5">
				<span
					class="inline-block h-2 w-2 rounded-full"
					style={statusDotStyle(level, color)}
					aria-hidden="true"
				></span>
				<span style={`color: ${color}`}>
					{maxSpawns === null ? 'unknown' : `${activeSpawns}/${maxSpawns} in use`}
				</span>
			</span>
		</div>
		<div
			class="h-2 w-full overflow-hidden border border-stone-800/80 bg-stone-900"
			role="img"
			aria-label={`spawn slots: ${activeSpawns} of ${maxSpawns ?? 'unknown'} in use`}
		>
			<div
				class="h-full transition-[width] duration-500 ease-out"
				style={`width: ${headroomPct ?? 0}%; ${statusBarStyle(level, color)}`}
			></div>
		</div>
		<div class="mt-1 text-right font-mono text-[11px] text-stone-500">
			concurrent worker-stack children, `spawn.max_concurrent`
		</div>
	</div>
</div>
