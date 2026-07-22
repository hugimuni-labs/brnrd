<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { DIAL_WEDGE_RADIUS, dialDasharray, fuelRows, runnerBlocks } from './controlStrip';
	import { quotaLevel, type QuotaShell } from './quota';
	import type { RunnersResponse } from './runners';
	import type { ConnectedRepo, EnvironmentOption } from './repos';
	import type { RunLedgerRow } from './runLedger';
	import type { ScheduledWake } from './scheduledWakes';
	import { readTanks, type TankVerdict } from './tankForecast';
	import {
		STATUS_BURNING,
		STATUS_COOLING,
		STATUS_SPENT,
		STATUS_UNKNOWN,
		statusBarStyle
	} from './statusPalette';

	interface Props {
		runners: RunnersResponse | null;
		shells: QuotaShell[] | null;
		runnersError?: string | null;
		runnersNote?: string | null;
		onTap?: (profileName: string, repoLabel: string | null, environment: string | null) => void;
		repos?: ConnectedRepo[] | null;
		/** Slice 2 inputs. Both optional: the strip's first two regions must
		 *  keep working on a page (or a test) that has no ledger or schedule. */
		ledgerRows?: RunLedgerRow[] | null;
		scheduledWakes?: ScheduledWake[] | null;
		now?: number;
	}

	let {
		runners,
		shells,
		runnersError = null,
		runnersNote = null,
		onTap,
		repos = null,
		ledgerRows = null,
		scheduledWakes = null,
		now = Date.now()
	}: Props = $props();
	let expanded = $state(false);
	let repoSelection = $state<string | null>(null);
	let environmentSelection = $state<string | null>(null);
	let selectedRepo = $derived(
		(repos ?? []).find(
			(repo) => repo.repo_full_name === (repoSelection ?? runners?.wake_request?.repo_label)
		) ??
			(repos ?? []).find((repo) => repo.dispatch_default) ??
			(repos ?? [])[0]
	);
	let environmentOptions = $derived<EnvironmentOption[]>(selectedRepo?.environments ?? []);
	// The collapsed strip shows the *resolved* environment — the thing the
	// next wake will actually run in — not the mechanism that resolved it.
	// "repo policy → host" read as an alias, not a choice (2026-07-22 round);
	// the mechanism note lives in the expanded panel where it can explain
	// itself.
	let environmentLabel = $derived(
		environmentSelection ??
			(selectedRepo?.environment_default
				? `${selectedRepo.environment_default} · default`
				: 'default')
	);
	let blocks = $derived(
		runnerBlocks(runners?.profiles ?? [], runners?.default ?? null, runners?.wake_request ?? null)
	);
	let fuel = $derived(fuelRows(shells ?? []));

	// The tank line: slice 2's whole visible surface. `readTanks` sorts worst
	// verdict first, and the strip is a glance instrument, so it shows the
	// leading one — the window about to run dry, not whichever shell the
	// provider listed first.
	let tanks = $derived(readTanks(shells ?? [], ledgerRows, scheduledWakes, now));
	let lead = $derived(tanks[0] ?? null);

	const VERDICT_COLOR: Record<TankVerdict, string> = {
		exhausting: STATUS_SPENT,
		tight: STATUS_BURNING,
		sustainable: STATUS_COOLING,
		unknown: STATUS_UNKNOWN
	};

	const LEVEL_COLOR: Record<string, string> = {
		burning: STATUS_BURNING,
		cooling: STATUS_COOLING,
		spent: STATUS_SPENT,
		unknown: STATUS_UNKNOWN
	};

	function profileTitle(name: string): string {
		const profile = runners?.profiles.find((candidate) => candidate.name === name);
		return profile ? `${profile.shell ?? '?'} · ${profile.model ?? 'default'}` : name;
	}

	function selectRepo(repo: ConnectedRepo) {
		repoSelection = repo.repo_full_name;
		environmentSelection = null;
	}

	function tapRunner(profileName: string) {
		onTap?.(profileName, selectedRepo?.repo_full_name ?? null, environmentSelection);
	}
</script>

<div class="panel mt-4">
	<div class="grid md:grid-cols-[minmax(13rem,0.9fr)_minmax(0,1.1fr)]">
		<button
			type="button"
			class="group min-w-0 cursor-pointer border-b border-stone-800/70 p-2.5 text-left hover:bg-amber-950/20 md:border-r md:border-b-0"
			aria-expanded={expanded}
			onclick={() => (expanded = !expanded)}
		>
			<div
				class="mb-1 flex items-center justify-between gap-2 font-mono text-[9px] tracking-[0.13em] text-ink-quiet uppercase"
			>
				<span>next wake · dispatch</span>
				<span class="text-ink-mute group-hover:text-stone-400" aria-hidden="true"
					>{expanded ? '▾' : '▸'} rack</span
				>
			</div>
			{#if runners === null}
				<div class="font-mono text-xs text-ink-quiet">next wake · loading…</div>
			{:else if blocks.length === 0}
				<div class="font-mono text-xs text-ink-quiet">next wake · unavailable</div>
			{:else}
				<!-- One chip grammar for every slot: value on top, role beneath,
				     ▾ on the slots the rack below can change. The old shape — a
				     flat text line plus styled runner blocks — hid that project
				     and environment were choices at all (2026-07-22 round). -->
				<div class="flex min-w-0 flex-wrap items-stretch gap-1.5">
					<span class="min-w-0 border border-stone-800/60 bg-stone-950/30 px-2 py-1 font-mono">
						<span class="block truncate text-[11px] font-medium text-stone-300">
							{selectedRepo?.repo_full_name ?? 'no project'}
						</span>
						<span
							class="mt-0.5 block truncate text-[8px] tracking-[0.11em] text-ink-quiet uppercase"
							>project ▾</span
						>
					</span>
					<span class="min-w-0 border border-stone-800/60 bg-stone-950/30 px-2 py-1 font-mono">
						<span class="block truncate text-[11px] font-medium text-stone-300"
							>{environmentLabel}</span
						>
						<span
							class="mt-0.5 block truncate text-[8px] tracking-[0.11em] text-ink-quiet uppercase"
							>environment ▾</span
						>
					</span>
					{#each blocks as block (block.kind)}
						<span
							title={profileTitle(block.profile.name)}
							class="min-w-0 border px-2 py-1 font-mono {block.active
								? 'border-amber-700/70 bg-amber-950/55 text-amber-100'
								: 'border-stone-800/60 bg-stone-950/30 text-ink-quiet opacity-55'}"
						>
							<span class="block truncate text-[11px] font-medium">{block.profile.name}</span>
							<span
								class="mt-0.5 block truncate text-[8px] tracking-[0.11em] uppercase {block.kind ===
								'requested'
									? 'text-amber-300'
									: 'text-sky-300'}">{block.badge} ▾</span
							>
						</span>
					{/each}
				</div>
			{/if}
		</button>

		<div class="min-w-0 p-2.5" aria-label="quota fuel">
			<div class="mb-1 font-mono text-[9px] tracking-[0.13em] text-ink-quiet uppercase">fuel</div>
			{#if shells === null}
				<div class="font-mono text-[10px] text-ink-mute">loading quota…</div>
			{:else if fuel.length === 0}
				<div class="font-mono text-[10px] text-ink-mute">no quota report</div>
			{:else}
				<!-- Two columns, period. The page column is max-w-2xl, so this
				     region is ~370px on desktop; four columns cut each window to
				     ~90px and made the grid the strip's least legible corner
				     (2026-07-22 round: "the fuel on the right is the worst"). -->
				<div class="grid grid-cols-2 gap-x-4 gap-y-1.5">
					{#each fuel as row (row.id)}
						{@const level = quotaLevel(row.percent)}
						<div class="min-w-0" title={row.tooltip}>
							<div
								class="mb-0.5 flex items-baseline justify-between gap-1 font-mono text-[9px] {row.stale
									? 'text-ink-mute'
									: 'text-stone-400'}"
							>
								<span class="truncate">{row.label}</span>
								<span class="flex items-center gap-1">
									{#if row.timeFraction !== null}
										<!-- The window's own clock, drawn as one: a disc
										     that fills as the window elapses and resets
										     full-empty. The previous shape — a second bar
										     under the fuel bar — borrowed the fuel bar's
										     grammar while meaning time, and nothing on
										     screen said so. -->
										<svg
											viewBox="0 0 12 12"
											class="h-[9px] w-[9px] -rotate-90 {row.stale ? 'opacity-40' : ''}"
											aria-hidden="true"
										>
											<circle
												cx="6"
												cy="6"
												r="5.5"
												fill="none"
												stroke-width="1"
												class="stroke-stone-800"
											/>
											<circle
												cx="6"
												cy="6"
												r={DIAL_WEDGE_RADIUS}
												fill="none"
												stroke-width={DIAL_WEDGE_RADIUS * 2}
												class="stroke-stone-500"
												stroke-dasharray={dialDasharray(row.timeFraction)}
											/>
										</svg>
									{/if}
									{#if row.resetShort}
										<span class="text-ink-quiet">↻{row.resetShort}</span>
									{/if}
									<span style={`color: ${LEVEL_COLOR[level]}`}>{row.percentLabel}</span>
								</span>
							</div>
							<div class="h-[3px] w-full bg-stone-900" role="img" aria-label={row.tooltip}>
								<div
									class="h-full transition-[width] duration-500 ease-out {row.stale
										? 'opacity-50'
										: ''}"
									style={`width: ${row.percent ?? 0}%; ${statusBarStyle(level, LEVEL_COLOR[level])}`}
								></div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	{#if lead}
		<!-- Slice 2 (design-wyrd §4 band 1). The fuel bars above answer "how
		     much is left"; this answers "does it last", which is the question
		     the two bars were already carrying between them and making the
		     reader compute by eye. Measured from the window's own numbers —
		     `100 - percent` drawn over the elapsed share of the window — so it
		     costs no join and cannot disagree with the bar above it.

		     Deliberately one line for the leading window only: this is a glance
		     strip. The per-window detail is the fuel grid; the verdict is here. -->
		<div
			class="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-stone-800/70 px-2.5 py-2 font-mono text-[10px]"
			aria-label="tank forecast"
		>
			<span class="tracking-[0.13em] text-ink-quiet uppercase">tank</span>
			<span class="text-stone-400">{lead.label}</span>
			<span style={`color: ${VERDICT_COLOR[lead.verdict]}`}>{lead.headline}</span>
			{#if lead.ratePerHour !== null}
				<!-- The rate names its source. `measured` is the recent-burn series
				     (#491/#493) — the current pace, read from sampled levels over
				     the last few hours; `window avg` is whole-window arithmetic,
				     which lags the pace by however much of the window already
				     happened. They answer different questions and the reader
				     deciding whether to dispatch deserves to know which one is
				     speaking. -->
				<span
					class="text-ink-mute"
					title={lead.rateSource === 'measured'
						? `current pace, measured over the last ${Math.round((lead.rateSpanMinutes ?? 0) / 60)}h of samples`
						: 'average draw across this whole window so far'}
				>
					{lead.ratePerHour < 1 ? lead.ratePerHour.toFixed(1) : Math.round(lead.ratePerHour)}%/h
					{lead.rateSource === 'measured' ? '· measured' : '· window avg'}
				</span>
			{/if}
			{#if lead.committedDraw !== null}
				<!-- The half the window cannot know: what is already queued to
				     draw on it. Priced from runs the daemon tagged
				     `source_system=schedule`, never from a self-reported slug. -->
				<span class="text-ink-quiet" title="scheduled wakes queued before this window resets">
					· {lead.committedWakes} scheduled ≈ {lead.committedDraw < 1
						? lead.committedDraw.toFixed(1)
						: Math.round(lead.committedDraw)}%
				</span>
			{:else if lead.committedWakes > 0}
				<!-- Count without a price: the wakes are real, the per-wake cost
				     is not yet measurable. Saying so beats inventing a number. -->
				<span class="text-ink-mute">· {lead.committedWakes} scheduled, cost unmeasured</span>
			{/if}
			{#if lead.stale}
				<span class="text-ink-mute">· stale report</span>
			{/if}
		</div>
	{/if}

	{#if expanded}
		<div class="border-t border-stone-800/70 p-3">
			<!-- Action receipts live with the control that caused them; keeping
			     them in the expanded rack avoids turning the glance strip into a
			     transient status-message row. -->
			{#if runnersError}
				<p class="mb-2 text-sm text-red-400">{runnersError}</p>
			{/if}
			{#if runnersNote}
				<p class="mb-2 font-mono text-xs text-amber-300">{runnersNote}</p>
			{/if}
			{#if runners === null}
				{#if !runnersError}
					<p class="text-sm text-ink-quiet">Loading…</p>
				{/if}
			{:else}
				<div class="mb-3 grid gap-3 lg:grid-cols-2">
					<div class="panel p-4">
						<div class="mb-3 font-mono text-sm font-medium tracking-wide text-amber-200 uppercase">
							project
						</div>
						{#if repos === null}
							<p class="font-mono text-xs text-ink-quiet">Loading account projects…</p>
						{:else if repos.length === 0}
							<p class="font-mono text-xs text-ink-quiet">No connected projects.</p>
						{:else}
							<div class="space-y-1.5">
								{#each repos as repo (repo.id)}
									{@const selected = selectedRepo?.id === repo.id}
									{@const dispatchable = repo.daemon_status === 'online'}
									<!-- A project without a live daemon cannot take a dispatch;
									     offering it as a selectable target promises a wake nobody
									     will serve (2026-07-22 round). Same disabled grammar as
									     the environment options below. -->
									<button
										type="button"
										disabled={!dispatchable}
										title={dispatchable
											? `dispatch to ${repo.repo_full_name}`
											: `daemon ${repo.daemon_status} — cannot dispatch here`}
										onclick={() => selectRepo(repo)}
										class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {dispatchable
											? selected
												? 'border-amber-700/70 bg-amber-950/30'
												: 'border-stone-800/60 bg-stone-900/30 hover:border-stone-600/70'
											: 'cursor-not-allowed border-stone-900/60 bg-stone-950/30 opacity-45'}"
									>
										<span
											class="truncate font-mono text-xs {!dispatchable
												? 'text-ink-mute'
												: selected
													? 'text-amber-200'
													: 'text-stone-300'}"
										>
											{dispatchable ? '' : '✗ '}{repo.repo_full_name}
										</span>
										<span
											class="flex shrink-0 items-baseline gap-2 font-mono text-[10px] uppercase"
										>
											{#if repo.dispatch_default}<span class="text-sky-300">default</span>{/if}
											<span
												class={repo.daemon_status === 'online' ? 'text-stone-400' : 'text-ink-mute'}
											>
												{repo.daemon_status}
											</span>
										</span>
									</button>
								{/each}
							</div>
						{/if}
					</div>

					<div class="panel p-4">
						<div class="mb-3 font-mono text-sm font-medium tracking-wide text-amber-200 uppercase">
							environment
						</div>
						<div class="space-y-1.5">
							<button
								type="button"
								onclick={() => (environmentSelection = null)}
								class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {environmentSelection ===
								null
									? 'border-amber-700/70 bg-amber-950/30'
									: 'border-stone-800/60 bg-stone-900/30 hover:border-stone-600/70'}"
							>
								<!-- Named by what it resolves to, not by the mechanism:
								     "repo policy" alone read as an alias the reader had to
								     go dereference (2026-07-22 round). The mechanism stays
								     as the badge — it explains *why* this is the default. -->
								<span class="font-mono text-xs text-amber-200">
									default{selectedRepo?.environment_default
										? ` — ${selectedRepo.environment_default}`
										: ''}
								</span>
								<span class="font-mono text-[10px] text-sky-300 uppercase">from repo policy</span>
							</button>
							{#each environmentOptions as option (option.name)}
								<button
									type="button"
									disabled={!option.available}
									title={option.reason ?? `next wake in ${option.name}`}
									onclick={() => (environmentSelection = option.name)}
									class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {option.available
										? environmentSelection === option.name
											? 'border-amber-700/70 bg-amber-950/30'
											: 'border-stone-800/60 bg-stone-900/30 hover:border-stone-600/70'
										: 'cursor-not-allowed border-stone-900/60 bg-stone-950/30 opacity-45'}"
								>
									<span
										class="font-mono text-xs {option.available
											? 'text-stone-300'
											: 'text-ink-mute'}"
									>
										{option.available ? '' : '✗ '}{option.name}
									</span>
									{#if !option.available}
										<span class="truncate font-mono text-[10px] text-ink-mute">{option.reason}</span
										>
									{/if}
								</button>
							{/each}
							{#if environmentOptions.length === 0}
								<p class="px-2 font-mono text-[10px] text-ink-mute">
									No daemon availability report.
								</p>
							{/if}
						</div>
					</div>
				</div>
				<SpoolRack
					profiles={runners.profiles}
					defaultProfile={runners.default}
					stale={runners.stale}
					wakeRequest={runners.wake_request ?? null}
					onTap={tapRunner}
				/>
			{/if}
		</div>
	{/if}
</div>
