<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { untilText, type ScheduledWake } from './scheduledWakes';
	import { typeReveal } from './transitions';
	import { STATUS_UNKNOWN, THERMAL_STOPS, statusDotStyle, type GlowUrgency } from './statusPalette';

	interface Props {
		wakes: ScheduledWake[];
		now: number;
	}

	let { wakes, now }: Props = $props();

	// Loom slice 4 (kb/design-continuous-presence.md §3.2.1): queued intent.
	// Live-runs shows *now*, receipts show *closed* — this lane shows *next*:
	// every `at:`/`every:` entry the daemons' schedule files hold, on a time
	// ruler you can watch approach. The C2 loop's first governor instrument:
	// round-the-clock autonomy you can see coming, not discover by receipt.
	//
	// The ruler is proportional, not linear-per-entry: markers sit where
	// their fire instant falls between `now` and the horizon (furthest wake,
	// floored at 1h so a single near wake doesn't fill the track). Overdue
	// markers pin to the left edge — visibly late, not silently absent (the
	// "director ticks stuck 2+ days" failure class this lane exists to
	// surface).

	const MIN_HORIZON_MS = 60 * 60 * 1000;
	const DUE_SOON_MS = 15 * 60 * 1000;

	const sorted = $derived(
		[...wakes].sort((a, b) => {
			const ta = a.scheduled_for ? Date.parse(a.scheduled_for) : Infinity;
			const tb = b.scheduled_for ? Date.parse(b.scheduled_for) : Infinity;
			return ta - tb;
		})
	);

	const horizonMs = $derived(
		Math.max(
			MIN_HORIZON_MS,
			...sorted
				.map((w) => (w.scheduled_for ? Date.parse(w.scheduled_for) - now : NaN))
				.filter((d) => !Number.isNaN(d) && d > 0)
		)
	);

	function markerPos(wake: ScheduledWake): number | null {
		if (!wake.scheduled_for) return null;
		const t = Date.parse(wake.scheduled_for);
		if (Number.isNaN(t)) return null;
		return Math.max(0, Math.min(1, (t - now) / horizonMs));
	}

	function markerColor(wake: ScheduledWake): string {
		if (!wake.scheduled_for) return STATUS_UNKNOWN;
		const dt = Date.parse(wake.scheduled_for) - now;
		if (Number.isNaN(dt)) return STATUS_UNKNOWN;
		if (dt <= 0) return THERMAL_STOPS.ash;
		return dt <= DUE_SOON_MS ? THERMAL_STOPS.amber : THERMAL_STOPS.frost;
	}

	function markerUrgency(wake: ScheduledWake): GlowUrgency {
		if (!wake.scheduled_for) return 'calm';
		const dt = Date.parse(wake.scheduled_for) - now;
		if (Number.isNaN(dt) || dt > DUE_SOON_MS) return 'calm';
		return dt <= 0 ? 'alarm' : 'attention';
	}

	function horizonLabel(ms: number): string {
		const h = ms / 3_600_000;
		if (h < 1.5) return `+${Math.round(ms / 60_000)}m`;
		if (h < 48) return `+${Math.round(h)}h`;
		return `+${Math.round(h / 24)}d`;
	}
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">scheduled wakes</span
		>
		<span class="font-mono text-[10px] tracking-wide text-stone-600 uppercase">
			{sorted.length} queued
		</span>
	</div>
	{#if sorted.length === 0}
		<p class="text-sm text-stone-500">Nothing queued — every wake right now is a spoken one.</p>
	{:else}
		<!-- The horizon ruler: now → furthest wake. Each marker is a wake
		     approaching the left edge; the daemon fires it when it arrives. -->
		<div class="mb-4" aria-hidden="true">
			<div class="relative h-2 bg-stone-900">
				{#each sorted as wake (wake.id)}
					{@const pos = markerPos(wake)}
					{#if pos !== null}
						{@const color = markerColor(wake)}
						<span
							class="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
							style={`left: ${(pos * 100).toFixed(2)}%; ${statusDotStyle('burning', color, markerUrgency(wake))}`}
							title={wake.summary}
						></span>
					{/if}
				{/each}
			</div>
			<div
				class="mt-1 flex justify-between font-mono text-[9px] tracking-wide text-stone-600 uppercase"
			>
				<span>now</span>
				<span>{horizonLabel(horizonMs)}</span>
			</div>
		</div>
		<div class="space-y-2">
			{#each sorted as wake (wake.id)}
				{@const due = untilText(wake.scheduled_for, now)}
				{@const color = markerColor(wake)}
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
								style={statusDotStyle('burning', color, markerUrgency(wake))}
								aria-hidden="true"
							></span>
							<span
								class="shrink-0 font-mono font-medium tracking-wide uppercase"
								style={`color: ${color}`}
							>
								{due ?? 'anchoring'}
							</span>
							<!-- `every` = a standing pulse; `at` = a one-shot deadline.
							     The trigger kind is the difference between "this will
							     keep happening" and "this happens once" — worth a chip. -->
							<span
								class="shrink-0 border border-stone-800 bg-stone-950/40 px-1 py-0.5 font-mono text-[9px] tracking-wide text-stone-500 uppercase"
								>{wake.phase === 'every' ? 'recurring' : 'one-shot'}</span
							>
						</span>
						<span class="shrink-0 font-mono text-[10px] text-stone-600">
							{wake.repo_label ?? ''}
						</span>
					</div>
					<p
						class="mt-1.5 truncate text-stone-300"
						title={wake.summary}
						use:typeReveal={{ text: wake.summary }}
					>
						{wake.summary}
					</p>
					{#if wake.conversation_key}
						<p class="truncate font-mono text-[10px] text-stone-600">
							→ {wake.conversation_key}
						</p>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
