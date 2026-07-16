<script lang="ts">
	import { glitchReveal, typeReveal } from './transitions';
	import { relicIcon, type RelicRecord, type RunLedgerRow } from './runLedger';
	import type { LiveRun } from './liveRuns';
	import type { ScheduledWake } from './scheduledWakes';
	import {
		LOOM_CENTER_ZONE_PX,
		LOOM_DUE_SOON_MS,
		LOOM_PAST_WINDOW_MS,
		loomFutureHorizon,
		loomFuturePosition,
		loomFutureStop,
		loomPastPosition,
		loomPastStop
	} from './loomBand';
	import {
		STATUS_BURNING,
		THERMAL_STOPS,
		glowFor,
		statusDotStyle,
		type GlowUrgency
	} from './statusPalette';

	interface Props {
		ledgerRows: RunLedgerRow[] | null;
		liveRuns: LiveRun[] | null;
		scheduledWakes: ScheduledWake[] | null;
		now: number;
	}

	let { ledgerRows, liveRuns, scheduledWakes, now }: Props = $props();

	interface PastTick {
		id: string;
		ageMs: number;
		position: number;
		color: string;
		prs: number;
		commits: number;
		kb: number;
	}

	function isKb(relic: RelicRecord): boolean {
		return relic.kind === 'kb' || relic.kind === 'kb_page';
	}

	function pastTicks(rows: RunLedgerRow[], timestamp: number): PastTick[] {
		const grouped: Array<{ id: string; endedAt: number; relics: RelicRecord[] }> = [];
		for (const row of rows) {
			const endedAt = row.ended_at ? Date.parse(row.ended_at) : Number.NaN;
			const ageMs = timestamp - endedAt;
			if (!Number.isFinite(endedAt) || ageMs < 0 || ageMs > LOOM_PAST_WINDOW_MS) continue;
			const id = row.run_id ?? row.event_id ?? row.ended_at ?? '';
			if (!id) continue;
			const current = grouped.find((group) => group.id === id);
			if (current) {
				current.endedAt = Math.max(current.endedAt, endedAt);
				current.relics.push(...(row.external_refs ?? []));
			} else {
				grouped.push({ id, endedAt, relics: [...(row.external_refs ?? [])] });
			}
		}

		return grouped
			.map((group) => {
				const ageMs = timestamp - group.endedAt;
				return {
					id: group.id,
					ageMs,
					position: loomPastPosition(ageMs),
					color: THERMAL_STOPS[loomPastStop(ageMs)],
					prs: group.relics.filter((relic) => relic.kind === 'pr').length,
					commits: group.relics.filter((relic) => relic.kind === 'commit').length,
					kb: group.relics.filter(isKb).length
				};
			})
			.filter((tick) => tick.prs + tick.commits + tick.kb > 0)
			.sort((a, b) => b.ageMs - a.ageMs);
	}

	let ticks = $derived(pastTicks(ledgerRows ?? [], now));
	let wakes = $derived(
		[...(scheduledWakes ?? [])].filter((wake) => {
			const instant = wake.scheduled_for ? Date.parse(wake.scheduled_for) : Number.NaN;
			return Number.isFinite(instant);
		})
	);
	let futureHorizon = $derived(
		loomFutureHorizon(
			wakes.map((wake) => wake.scheduled_for),
			now
		)
	);

	function wakeEta(wake: ScheduledWake): number {
		return wake.scheduled_for ? Date.parse(wake.scheduled_for) - now : Number.NaN;
	}

	function wakeColor(wake: ScheduledWake): string {
		const eta = wakeEta(wake);
		if (eta < 0) return THERMAL_STOPS.ash;
		return THERMAL_STOPS[loomFutureStop(eta, futureHorizon)];
	}

	function wakeUrgency(wake: ScheduledWake): GlowUrgency {
		const eta = wakeEta(wake);
		if (eta < 0) return 'alarm';
		return eta <= LOOM_DUE_SOON_MS ? 'attention' : 'calm';
	}

	function ageLabel(ms: number): string {
		const minutes = Math.max(0, Math.round(ms / 60_000));
		if (minutes < 60) return `${minutes}m ago`;
		return `${Math.floor(minutes / 60)}h ${minutes % 60}m ago`;
	}

	function etaLabel(ms: number): string {
		const minutes = Math.round(Math.abs(ms) / 60_000);
		if (ms < 0) return `${minutes}m overdue`;
		if (minutes < 60) return `in ${minutes}m`;
		return `in ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
	}

	function scrollToDetail(kind: 'run' | 'wake', id: string) {
		const attribute = kind === 'run' ? 'loomRun' : 'loomWake';
		const target = [
			...document.querySelectorAll<HTMLElement>('[data-loom-run], [data-loom-wake]')
		].find((element) => element.dataset[attribute] === id);
		if (!target) return;
		const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
	}

	function runText(run: LiveRun): string {
		const label = run.label || run.kind || run.repo_label || 'live run';
		return run.card_text ? `${label} · ${run.card_text}` : label;
	}
</script>

<div
	class="panel overflow-hidden px-3 py-2.5"
	aria-label="24-hour past, live runs now, and scheduled future"
>
	<div
		class="grid items-center font-mono text-[9px] tracking-[0.16em] text-stone-600 uppercase"
		style={`grid-template-columns: minmax(0, 1fr) ${LOOM_CENTER_ZONE_PX}px minmax(0, 1fr)`}
	>
		<span>past · 24h</span>
		<span class="text-center text-amber-200">now</span>
		<span class="text-right">future</span>
	</div>

	<div
		class="mt-1 grid h-[72px]"
		style={`grid-template-columns: minmax(0, 1fr) ${LOOM_CENTER_ZONE_PX}px minmax(0, 1fr)`}
	>
		<div class="relative min-w-0" aria-label="produce from the past 24 hours">
			<div class="absolute top-9 right-0 left-0 h-px bg-stone-800" aria-hidden="true"></div>
			<span class="absolute top-[31px] left-0 text-[9px] text-stone-700" aria-hidden="true">←</span>
			{#if ledgerRows !== null && ticks.length === 0}
				<span
					class="absolute inset-x-2 top-3 truncate text-center font-mono text-[9px] text-stone-700"
				>
					no produce in 24h
				</span>
			{/if}
			{#each ticks as tick, index (tick.id)}
				<button
					type="button"
					class="absolute top-3 flex h-10 -translate-x-1/2 cursor-pointer items-end gap-px px-1"
					style={`left: ${(tick.position * 100).toFixed(3)}%; color: ${tick.color}`}
					title={`${tick.id} · ${tick.prs} PR · ${tick.commits} commit · ${tick.kb} kb · ${ageLabel(tick.ageMs)}`}
					onclick={() => scrollToDetail('run', tick.id)}
					in:glitchReveal={{ duration: 240, delay: index * 24 }}
				>
					{#if tick.prs > 0}
						<span
							class="block h-7 w-0.5"
							style={`background-color: ${tick.color}`}
							aria-label={`${tick.prs} PR${tick.prs === 1 ? '' : 's'}`}
						></span>
					{/if}
					{#if tick.commits > 0}
						<span
							class="block h-3 w-0.5 opacity-80"
							style={`background-color: ${tick.color}`}
							aria-label={`${tick.commits} commit${tick.commits === 1 ? '' : 's'}`}
						></span>
					{/if}
					{#if tick.kb > 0}
						<span
							class="block text-[9px] leading-none"
							aria-label={`${tick.kb} knowledge page${tick.kb === 1 ? '' : 's'}`}
							use:typeReveal={{ text: relicIcon('kb'), delay: index * 24 }}>{relicIcon('kb')}</span
						>
					{/if}
				</button>
			{/each}
		</div>

		<div class="relative z-10 border-x border-amber-900/40 bg-stone-950/70 px-1">
			<div class="absolute inset-x-0 top-9 h-px bg-amber-700/60" aria-hidden="true"></div>
			{#if liveRuns === null}
				<div
					class="absolute inset-0 flex items-center justify-center font-mono text-[9px] text-stone-700"
				>
					acquiring
				</div>
			{:else if liveRuns.length === 0}
				<div class="absolute inset-0 flex flex-col items-center justify-center">
					<span
						class="h-2.5 w-2.5 rounded-full border border-stone-600 bg-stone-950"
						aria-hidden="true"
					></span>
					<span class="mt-1 font-mono text-[9px] text-stone-500">
						{new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
					</span>
				</div>
			{:else}
				<div class="absolute inset-1 flex flex-col justify-center gap-1 overflow-hidden">
					{#each liveRuns.slice(0, 2) as run, index (run.id)}
						<button
							type="button"
							class="min-w-0 cursor-pointer border border-amber-700/50 bg-stone-950/90 px-1.5 py-1 text-left font-mono text-[9px] leading-tight text-amber-100"
							style={glowFor(liveRuns.length > 1 ? 'attention' : 'calm', STATUS_BURNING)}
							title={runText(run)}
							onclick={() => scrollToDetail('run', run.run_id || run.id)}
							in:glitchReveal={{ duration: 260, delay: 35 + index * 38 }}
						>
							<span
								class="block truncate"
								use:typeReveal={{
									text: run.label || run.kind || run.repo_label || 'live run',
									delay: 55 + index * 38
								}}
							>
								{run.label || run.kind || run.repo_label || 'live run'}
							</span>
							{#if run.card_text}
								<span
									class="mt-0.5 block truncate text-[8px] text-stone-400"
									use:typeReveal={{ text: run.card_text, delay: 72 + index * 38 }}
								>
									{run.card_text}
								</span>
							{/if}
						</button>
					{/each}
					{#if liveRuns.length > 2}
						<span class="text-center font-mono text-[8px] text-amber-500/70"
							>+{liveRuns.length - 2}</span
						>
					{/if}
				</div>
			{/if}
		</div>

		<div class="relative min-w-0" aria-label="scheduled wakes">
			<div class="absolute top-9 right-0 left-0 h-px bg-stone-800" aria-hidden="true"></div>
			<span class="absolute top-[31px] right-0 text-[9px] text-stone-700" aria-hidden="true">→</span
			>
			{#if scheduledWakes !== null && wakes.length === 0}
				<span
					class="absolute inset-x-2 top-3 truncate text-center font-mono text-[9px] text-stone-700"
				>
					nothing queued
				</span>
			{/if}
			{#each wakes as wake, index (wake.id)}
				{@const eta = wakeEta(wake)}
				{@const color = wakeColor(wake)}
				{@const urgency = wakeUrgency(wake)}
				<button
					type="button"
					class="absolute h-7 w-5 -translate-x-1/2 cursor-pointer"
					class:top-6={index % 2 === 0}
					class:top-8={index % 2 !== 0}
					style={`left: ${(loomFuturePosition(eta, futureHorizon) * 100).toFixed(3)}%`}
					title={`${wake.summary} · ${etaLabel(eta)}`}
					onclick={() => scrollToDetail('wake', wake.id)}
					in:glitchReveal={{ duration: 240, delay: 70 + index * 26 }}
				>
					<span
						class="absolute top-1/2 left-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
						style={statusDotStyle('burning', color, urgency)}
						aria-label={etaLabel(eta)}
					></span>
				</button>
			{/each}
		</div>
	</div>
</div>
