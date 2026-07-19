<script lang="ts">
	import { glitchReveal } from './transitions';
	import { durationLabel, type RelicRecord, type RunLedgerRow } from './runLedger';
	import { runNodeHref } from './runNode';
	import {
		LiveRunsAuthError,
		liveRunDisplayName,
		requestRunStop,
		type LiveRun
	} from './liveRuns';
	import type { ScheduledWake } from './scheduledWakes';
	import {
		LOOM_CENTER_ZONE_PX,
		LOOM_DUE_SOON_MS,
		LOOM_PAST_WINDOWS_MS,
		LOOM_PAST_WINDOW_MS,
		LOOM_STOP_ARM_WINDOW_MS,
		loomBarFraction,
		loomCellClickSelects,
		loomFutureHorizon,
		loomFutureStop,
		loomPastStop,
		loomPastWindowLabel,
		loomStopGesture
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
		/** Selection is the page's: the band reports, the detail sheet answers. */
		onSelect?: (kind: 'run' | 'wake', id: string) => void;
		onPastWindowChange?: (windowMs: number) => void;
		selectedId?: string | null;
		/**
		 * Seam for tests (#476). The band calls the endpoint itself rather than
		 * routing a stop up through the page: the affordance, its confirmation,
		 * and its receipt line are one thing, and splitting them across two
		 * files is how the receipt goes missing.
		 */
		stopRun?: (runId: string) => Promise<unknown>;
	}

	let {
		ledgerRows,
		liveRuns,
		scheduledWakes,
		now,
		onSelect,
		onPastWindowChange,
		selectedId = null,
		stopRun = requestRunStop
	}: Props = $props();

	// #476: the stop affordance's local state. `armedStopId` is the run whose
	// control is showing "stop?"; `stoppedIds` remembers runs stopped in this
	// session so the cell flips to "stopping" on the tap rather than waiting a
	// full poll for the server to agree.
	let armedStopId = $state<string | null>(null);
	let armedAt: number | null = null;
	let stoppedIds = $state<Set<string>>(new Set());
	let stopNote = $state<string | null>(null);

	async function tapStop(event: MouseEvent, runId: string) {
		// The cell behind this control selects on click; a stop must not also
		// be a selection, so the gesture stops here.
		event.stopPropagation();
		const gesture = loomStopGesture(event, armedStopId === runId ? armedAt : null, Date.now());
		if (gesture === 'ignore') return;
		if (gesture === 'arm') {
			armedStopId = runId;
			armedAt = Date.now();
			stopNote = 'tap again to stop — this does not resume';
			return;
		}
		armedStopId = null;
		armedAt = null;
		try {
			await stopRun(runId);
			stoppedIds = new Set(stoppedIds).add(runId);
			// Deliberately not "stopped": the daemon has not consumed it yet.
			stopNote = 'stopping — ends on the next daemon sync, partial work kept';
		} catch (e) {
			// A swallowed stop must be loud (the 2026-07-11 lesson): the user
			// just tried to kill a burning run and nothing happened.
			stopNote =
				e instanceof LiveRunsAuthError
					? 'session expired — sign in again, then re-tap'
					: e instanceof Error
						? e.message
						: 'stop request failed';
		}
	}

	// The arm lapses on its own, so a control left armed by a mis-tap can't be
	// committed by an unrelated click later. `now` already ticks for the band.
	$effect(() => {
		if (armedStopId !== null && armedAt !== null && now - armedAt > LOOM_STOP_ARM_WINDOW_MS) {
			armedStopId = null;
			armedAt = null;
			stopNote = null;
		}
	});

	// Past scrollback ("can't scroll back", 2026-07-16): a discrete window
	// over the past shelf. Click the label to step 6h → 12h → 24h → 3d → 7d.
	// (The wheel used to step it too; the shelf's own vertical scroll owns
	// the wheel now.)
	let pastWindowMs = $state<number>(LOOM_PAST_WINDOW_MS);

	function cyclePastWindow() {
		const index = LOOM_PAST_WINDOWS_MS.findIndex((window) => window >= pastWindowMs);
		pastWindowMs = LOOM_PAST_WINDOWS_MS[(index + 1) % LOOM_PAST_WINDOWS_MS.length];
		onPastWindowChange?.(pastWindowMs);
	}

	// The shelf model (2026-07-17 steer): one run = one horizontal bar row,
	// newest at the NOW seam. Length carries spend, color carries age, the
	// legend carries produce — in type, not pictograms.
	interface ShelfRun {
		id: string;
		/**
		 * Route to this run's Wyrd node, or null when the row has no real
		 * `run_id` (the shelf id then falls back to an event id or timestamp,
		 * which names no durable node — those rows keep the select-only
		 * behaviour instead of linking somewhere that can never resolve).
		 */
		href: string | null;
		ageMs: number;
		wallSeconds: number;
		color: string;
		legend: string;
		/** A run that closed without produce still happened — faint row. */
		bare: boolean;
	}

	function isKb(relic: RelicRecord): boolean {
		return relic.kind === 'kb' || relic.kind === 'kb_page';
	}

	function produceLegend(relics: RelicRecord[]): string {
		const prs = relics.filter((relic) => relic.kind === 'pr').length;
		const commits = relics.filter((relic) => relic.kind === 'commit').length;
		const kb = relics.filter(isKb).length;
		const parts = [
			prs > 0 ? `${prs}pr` : '',
			commits > 0 ? `${commits}c` : '',
			kb > 0 ? `${kb}kb` : ''
		].filter(Boolean);
		return parts.join(' ');
	}

	function shelfRuns(rows: RunLedgerRow[], timestamp: number, windowMs: number): ShelfRun[] {
		const grouped: Array<{
			id: string;
			runId: string | null;
			repoLabel: string | null;
			endedAt: number;
			wallSeconds: number;
			relics: RelicRecord[];
		}> = [];
		for (const row of rows) {
			const endedAt = row.ended_at ? Date.parse(row.ended_at) : Number.NaN;
			const ageMs = timestamp - endedAt;
			if (!Number.isFinite(endedAt) || ageMs < 0 || ageMs > windowMs) continue;
			const id = row.run_id ?? row.event_id ?? row.ended_at ?? '';
			if (!id) continue;
			const current = grouped.find((group) => group.id === id);
			if (current) {
				current.runId ??= row.run_id;
				current.repoLabel ??= row.repo_label;
				current.endedAt = Math.max(current.endedAt, endedAt);
				current.wallSeconds = Math.max(current.wallSeconds, row.wall_clock_seconds ?? 0);
				current.relics.push(...(row.external_refs ?? []));
			} else {
				grouped.push({
					id,
					runId: row.run_id,
					repoLabel: row.repo_label,
					endedAt,
					wallSeconds: row.wall_clock_seconds ?? 0,
					relics: [...(row.external_refs ?? [])]
				});
			}
		}

		return grouped
			.map((group) => {
				const ageMs = timestamp - group.endedAt;
				const produce = produceLegend(group.relics);
				return {
					id: group.id,
					href: group.runId ? runNodeHref(group.repoLabel, group.runId) : null,
					ageMs,
					wallSeconds: group.wallSeconds,
					color: THERMAL_STOPS[loomPastStop(ageMs)],
					legend: produce
						? `${produce} · ${durationLabel(group.wallSeconds)}`
						: durationLabel(group.wallSeconds),
					bare: !produce
				};
			})
			.sort((a, b) => a.ageMs - b.ageMs);
	}

	let runs = $derived(shelfRuns(ledgerRows ?? [], now, pastWindowMs));
	let maxWallSeconds = $derived(Math.max(...runs.map((run) => run.wallSeconds), 0));
	let wakes = $derived(
		[...(scheduledWakes ?? [])]
			.filter((wake) => {
				const instant = wake.scheduled_for ? Date.parse(wake.scheduled_for) : Number.NaN;
				return Number.isFinite(instant);
			})
			.sort((a, b) => Date.parse(a.scheduled_for ?? '') - Date.parse(b.scheduled_for ?? ''))
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
		const hours = Math.floor(minutes / 60);
		if (hours < 48) return `${hours}h ${minutes % 60}m ago`;
		return `${Math.floor(hours / 24)}d ${hours % 24}h ago`;
	}

	function etaLabel(ms: number): string {
		const minutes = Math.round(Math.abs(ms) / 60_000);
		if (ms < 0) return `${minutes}m overdue`;
		if (minutes < 60) return `in ${minutes}m`;
		return `in ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
	}

	function wakeLegend(wake: ScheduledWake): string {
		const eta = wakeEta(wake);
		const summary = (wake.summary || wake.conversation_key || 'wake').trim();
		return `${etaLabel(eta)} · ${summary}`;
	}

	function select(kind: 'run' | 'wake', id: string) {
		onSelect?.(kind, id);
	}

	// A plain left click fills §2a and keeps the reader in the band; every
	// modified click still follows the anchor. The rule itself lives in
	// `loomBand.ts` (`loomCellClickSelects`) so it can be tested without a
	// browser — it is the whole of the defect this slice fixes.
	function selectFromCell(event: MouseEvent, id: string) {
		if (!loomCellClickSelects(event)) return;
		event.preventDefault();
		select('run', id);
	}

	function elapsedLabel(run: LiveRun): string {
		const started = run.started_at ? Date.parse(run.started_at) : Number.NaN;
		if (!Number.isFinite(started)) return '';
		return durationLabel(Math.max(0, (now - started) / 1000));
	}

	let nextWake = $derived(wakes.find((wake) => wakeEta(wake) > 0) ?? null);

	// One row geometry, whether the cell ends up a link or a button — the band
	// is a 128px instrument and the two must not drift a pixel apart.
	const SHELF_ROW_CLASS =
		'flex max-h-[22px] min-h-[11px] flex-1 shrink-0 cursor-pointer items-center justify-end gap-1.5';

	function shelfRowStyle(run: ShelfRun): string {
		return `color: ${run.color};${selectedId === run.id ? ' filter: brightness(1.6);' : ''}`;
	}
</script>

{#snippet shelfRow(run: ShelfRun)}
	<span
		class="truncate font-mono text-[9px] leading-none whitespace-nowrap"
		class:opacity-50={run.bare}
	>
		{run.legend}
	</span>
	<span
		class="h-[7px] shrink-0 rounded-l-[1px]"
		class:opacity-40={run.bare}
		style={`width: ${(loomBarFraction(run.wallSeconds, maxWallSeconds) * 62).toFixed(2)}%; background-color: ${run.color}`}
		aria-hidden="true"
	></span>
{/snippet}

<div
	class="panel overflow-hidden px-3 py-2.5"
	aria-label="past produce, live runs now, and scheduled future"
>
	<div
		class="grid items-center font-mono text-[9px] tracking-[0.16em] text-stone-600 uppercase"
		style={`grid-template-columns: minmax(0, 1fr) ${LOOM_CENTER_ZONE_PX}px minmax(0, 1fr)`}
	>
		<span>
			<button
				type="button"
				class="cursor-pointer uppercase hover:text-stone-400"
				title="step the past window: 6h → 12h → 24h → 3d → 7d"
				onclick={cyclePastWindow}
			>
				past · {loomPastWindowLabel(pastWindowMs)}
			</button>
			{#if runs.length > 0}
				<span class="ml-1 text-stone-700">· {runs.length} run{runs.length === 1 ? '' : 's'}</span>
			{/if}
		</span>
		<span class="text-center text-amber-200">now</span>
		<span class="text-right">
			future
			{#if wakes.length > 0}
				<span class="ml-1 normal-case text-stone-700">· {wakes.length}</span>
			{/if}
		</span>
	</div>

	<div
		class="mt-1 grid h-[128px]"
		style={`grid-template-columns: minmax(0, 1fr) ${LOOM_CENTER_ZONE_PX}px minmax(0, 1fr)`}
	>
		<!-- Past shelf: newest row at the top, bars anchored to the NOW seam,
		     length ∝ spend (sqrt-scaled), color = thermal age. Rows flex
		     between 11px and 22px so four runs fill the band as confidently
		     as twenty-six; past that, the shelf scrolls. -->
		<div
			class="loom-shelf flex min-w-0 flex-col gap-px overflow-y-auto pr-1.5"
			aria-label="closed runs in the selected past window"
		>
			{#if ledgerRows !== null && runs.length === 0}
				<span class="m-auto truncate font-mono text-[9px] text-stone-700">
					no runs in {loomPastWindowLabel(pastWindowMs)}
				</span>
			{/if}
			<!-- A closed run is a *place*, so its cell is a real link into that
			     run's Wyrd node — right-clickable, openable in a tab, and a URL
			     you can send someone. A plain left click, though, fills the
			     detail frame below rather than navigating: the loom is the spine
			     and the reader keeps their place (see `selectFromCell`). Rows
			     with no durable run id are select-only; identical geometry. -->
			{#each runs as run, index (run.id)}
				{#if run.href}
					<a
						href={run.href}
						class={SHELF_ROW_CLASS}
						style={shelfRowStyle(run)}
						title={`${run.id} · ${run.legend} · ${ageLabel(run.ageMs)} — click to open below, ctrl/⌘-click for the full node`}
						onclick={(event) => selectFromCell(event, run.id)}
						in:glitchReveal={{ duration: 240, delay: index * 24 }}
					>
						{@render shelfRow(run)}
					</a>
				{:else}
					<button
						type="button"
						class={SHELF_ROW_CLASS}
						style={shelfRowStyle(run)}
						title={`${run.id} · ${run.legend} · ${ageLabel(run.ageMs)}`}
						onclick={() => select('run', run.id)}
						in:glitchReveal={{ duration: 240, delay: index * 24 }}
					>
						{@render shelfRow(run)}
					</button>
				{/if}
			{/each}
		</div>

		<!-- The NOW seam: an instrument, not a snapshot. Idle it answers
		     "when does the next thing happen"; active it answers "what is
		     running and for how long". Everything else is the sheet's job. -->
		<div class="relative z-10 border-x border-amber-900/40 bg-stone-950/70 px-1">
			{#if liveRuns === null}
				<div
					class="absolute inset-0 flex items-center justify-center font-mono text-[9px] text-stone-700"
				>
					acquiring
				</div>
			{:else if liveRuns.length === 0}
				<div class="absolute inset-0 flex flex-col items-center justify-center gap-1">
					<span
						class="h-2.5 w-2.5 rounded-full border border-stone-600 bg-stone-950"
						aria-hidden="true"
					></span>
					<span class="font-mono text-[10px] text-stone-400">
						{new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
					</span>
					{#if nextWake}
						<span class="max-w-full truncate px-1 text-center font-mono text-[8px] text-stone-600">
							next {etaLabel(wakeEta(nextWake))}
						</span>
					{/if}
				</div>
			{:else}
				<div class="absolute inset-1 flex flex-col justify-center gap-1 overflow-hidden">
					{#each liveRuns.slice(0, 2) as run, index (run.id)}
						{@const stopId = run.run_id || run.id}
						{@const stopping = run.stop_requested || stoppedIds.has(stopId)}
						<div
							class="flex min-w-0 items-stretch gap-px"
							in:glitchReveal={{ duration: 260, delay: 35 + index * 38 }}
						>
							<button
								type="button"
								class="min-w-0 flex-1 cursor-pointer border border-amber-700/50 bg-stone-950/90 px-1.5 py-1 text-left font-mono leading-tight text-amber-100"
								style={glowFor(liveRuns.length > 1 ? 'attention' : 'calm', STATUS_BURNING)}
								title={liveRunDisplayName(run) || run.repo_label || 'live run'}
								onclick={() => select('run', stopId)}
							>
								<span class="block truncate text-[9px]">
									{liveRunDisplayName(run) || run.repo_label || 'live run'}
								</span>
								{#if elapsedLabel(run)}
									<span class="mt-0.5 block text-[8px] text-amber-500/80">
										{stopping ? 'stopping…' : elapsedLabel(run)}
									</span>
								{/if}
							</button>
							<!-- The stop affordance (#476 wyrd §3). Its own button beside
							     the cell, never nested inside it: the selecting click has a
							     settled grammar and a kill must not ride it. Arm-then-commit
							     (`loomStopGesture`) because a stopped thought does not
							     resume. Once parked it stays "stopping" — the daemon
							     consumes it on its next sync, and the cell must not claim a
							     terminal state the system has not reached. -->
							{#if stopping}
								<span
									class="flex w-7 shrink-0 items-center justify-center border border-amber-800/40 font-mono text-[8px] text-amber-600/80"
									title="stop requested — the daemon ends this run on its next sync"
								>
									···
								</span>
							{:else}
								<button
									type="button"
									class="w-7 shrink-0 cursor-pointer border border-red-900/60 bg-stone-950/90 font-mono text-[8px] text-red-400/90 hover:bg-red-950/40"
									title={armedStopId === stopId
										? 'tap again to stop this run — partial work is salvaged, the thought does not resume'
										: 'stop this run'}
									aria-label={`stop run ${liveRunDisplayName(run) || stopId}`}
									onclick={(event) => tapStop(event, stopId)}
								>
									{armedStopId === stopId ? 'stop?' : '×'}
								</button>
							{/if}
						</div>
					{/each}
					{#if stopNote}
						<!-- Receipt line: a tap that gets swallowed must never be silent
						     (found live 2026-07-11 on the spool rack's own taps). -->
						<span class="truncate text-center font-mono text-[8px] text-amber-400/90">
							{stopNote}
						</span>
					{/if}
					{#if liveRuns.length > 2}
						<span class="text-center font-mono text-[8px] text-amber-500/70"
							>+{liveRuns.length - 2}</span
						>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Future shelf: soonest at the top, bars anchored to the NOW seam,
		     length ∝ distance-to-fire against the horizon — a countdown you
		     can read as geometry. Frost thaws to amber as the fire nears. -->
		<div
			class="loom-shelf flex min-w-0 flex-col gap-px overflow-y-auto pl-1.5"
			aria-label="scheduled wakes"
		>
			{#if scheduledWakes !== null && wakes.length === 0}
				<span class="m-auto truncate font-mono text-[9px] text-stone-700"> nothing queued </span>
			{/if}
			{#each wakes as wake, index (wake.id)}
				{@const eta = wakeEta(wake)}
				{@const color = wakeColor(wake)}
				{@const urgency = wakeUrgency(wake)}
				<button
					type="button"
					class="flex max-h-[22px] min-h-[11px] flex-1 shrink-0 cursor-pointer items-center justify-start gap-1.5"
					style={`color: ${color};${selectedId === wake.id ? ' filter: brightness(1.6);' : ''}`}
					title={`${wake.summary} · ${etaLabel(eta)}`}
					onclick={() => select('wake', wake.id)}
					in:glitchReveal={{ duration: 240, delay: 70 + index * 26 }}
				>
					<span
						class="h-2 w-2 shrink-0 rounded-full"
						style={statusDotStyle('burning', color, urgency)}
						aria-hidden="true"
					></span>
					<span
						class="h-[7px] shrink-0 rounded-r-[1px]"
						style={`width: ${(loomBarFraction(Math.max(eta, 0), futureHorizon) * 38).toFixed(2)}%; background-color: ${color}`}
						aria-hidden="true"
					></span>
					<span class="truncate font-mono text-[9px] leading-none whitespace-nowrap">
						{wakeLegend(wake)}
					</span>
				</button>
			{/each}
		</div>
	</div>
</div>

<style>
	/* The shelf scrolls, but chrome must not: a scrollbar gutter inside a
	   112px instrument reads as clutter. Thin and dark where supported. */
	.loom-shelf {
		scrollbar-width: thin;
		scrollbar-color: var(--color-stone-800, #292524) transparent;
	}
</style>
