<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { glitchReveal } from './transitions';
	import {
		DIAL_WEDGE_RADIUS,
		dialDasharray,
		fuelRows,
		railIsSlim,
		runnerBlocks,
		slotChip
	} from './controlStrip';
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
	import {
		DISABLED_ROW,
		IDLE_ROW,
		SELECTED_ACTIVE,
		SELECTED_OPTION,
		UNAVAILABLE_MARK
	} from './stateChrome';

	interface Props {
		runners: RunnersResponse | null;
		shells: QuotaShell[] | null;
		runnersError?: string | null;
		runnersNote?: string | null;
		onTap?: (profileName: string, repoLabel: string | null, environment: string | null) => void;
		/** #932's exit tap: release the conversation-sticky early. Wired to
		 *  the rack chip's ✕; absent ⇒ the chip renders without an exit. */
		onReleaseSticky?: () => void;
		repos?: ConnectedRepo[] | null;
		/** Slice 2 inputs. Both optional: the strip's first two regions must
		 *  keep working on a page (or a test) that has no ledger or schedule. */
		ledgerRows?: RunLedgerRow[] | null;
		scheduledWakes?: ScheduledWake[] | null;
		now?: number;
		/** The spawn-slot capacity chip (#972 machine round: the LIMITS section
		 *  folded in here — slots are machine capacity, like fuel). `null`
		 *  spawns means the live-runs packet hasn't landed: no chip, rather
		 *  than a fabricated zero. */
		activeSpawns?: number | null;
		maxSpawns?: number | null;
		/** The page's scroll verdict (his 08-02 steer: the rail stays on top,
		 *  collapsed once the reader scrolls). True ⇒ render the one-line slim
		 *  bar unless the reader has opened the rail themselves. */
		condensed?: boolean;
		/** Fired when the *rack* opens or folds — the tall panel, not the slim
		 *  bar's peek. The page owns the scroll response (his 08-02 steer:
		 *  "when it's expanded it should just go to the top of the page, and
		 *  when it's collapsed, go back"). Deliberately not fired for
		 *  `pinnedOpen`: that form fits on screen, so moving the reader for it
		 *  would cost them their place to answer a glance. */
		onRackChange?: (open: boolean) => void;
	}

	let {
		runners,
		shells,
		runnersError = null,
		runnersNote = null,
		onTap,
		onReleaseSticky,
		repos = null,
		ledgerRows = null,
		scheduledWakes = null,
		now = Date.now(),
		activeSpawns = null,
		maxSpawns = null,
		condensed = false,
		onRackChange
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
		runnerBlocks(
			runners?.profiles ?? [],
			runners?.default ?? null,
			runners?.wake_request ?? null,
			runners?.sticky ?? null,
			now
		)
	);
	let fuel = $derived(fuelRows(shells ?? []));
	let slots = $derived(activeSpawns === null ? null : slotChip(activeSpawns, maxSpawns));

	// The tank line: slice 2's whole visible surface. `readTanks` sorts worst
	// verdict first, and the strip is a glance instrument, so it shows the
	// leading one — the window about to run dry, not whichever shell the
	// provider listed first.
	let tanks = $derived(readTanks(shells ?? [], ledgerRows, scheduledWakes, now));
	let lead = $derived(tanks[0] ?? null);

	// The scrolled form: one line of resource truth. `pinnedOpen` is the
	// reader's override of the scroll verdict, and it lapses when the page
	// un-condenses so the next scroll starts collapsed again.
	let pinnedOpen = $state(false);
	$effect(() => {
		if (!condensed) pinnedOpen = false;
	});

	// THE PICKER YOU CANNOT REACH (2026-08-02, his report — he could not select
	// `claude-fable` for the run that fixed this). The rule and its history live
	// on `railIsSlim`; the short version is that a reader's own open outranks
	// the page's scroll verdict, and expanding the rack is one of the two ways
	// to open. The page's scroll response — go to the top on open, return on
	// fold — is `onRackChange`'s half, and it is reported for the *rack* only:
	// the tall panel is the one that cannot fit on a scrolled screen.
	let slim = $derived(railIsSlim({ condensed, pinnedOpen, expanded }));
	let reportedRack = false;
	$effect(() => {
		const open = expanded;
		if (open === reportedRack) return;
		reportedRack = open;
		onRackChange?.(open);
	});
	let activeBlock = $derived(blocks.find((block) => block.active) ?? null);

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

{#if slim}
	<!-- The rail, scrolled: fuel, slots, tank verdict, and where the next
	     pick runs — one line, always in reach. Tap to unfold the full rail
	     in place (his 08-02 steer: "the resource management should stay on
	     top, maybe in a collapsed way"). -->
	<button
		type="button"
		class="panel panel--pressable panel--collapsed flex w-full flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-1.5 text-left font-mono text-[10px]"
		aria-expanded="false"
		aria-label="expand the rail"
		onclick={() => (pinnedOpen = true)}
	>
		<span class="tracking-[0.13em] text-ink-quiet uppercase">▸ rail</span>
		{#if activeBlock}
			<span class="text-amber-200" title={profileTitle(activeBlock.profile.name)}
				>{activeBlock.profile.name}</span
			>
		{/if}
		{#each fuel as row (row.id)}
			{@const level = quotaLevel(row.percent)}
			<span
				class="whitespace-nowrap text-ink-quiet {row.stale || row.daemonStale ? 'opacity-60' : ''}"
				title={row.tooltip}
			>
				{row.label}
				<span style={`color: ${LEVEL_COLOR[level]}`}
					>{row.percent === null ? '?' : `${Math.round(row.percent)}%`}</span
				>
			</span>
		{/each}
		{#if slots}
			<span
				title={slots.title}
				class="text-ink-quiet"
				style={slots.level ? `color: ${LEVEL_COLOR[slots.level]}` : ''}>{slots.label}</span
			>
		{/if}
		{#if lead}
			<span style={`color: ${VERDICT_COLOR[lead.verdict]}`}>{lead.verdict}</span>
		{/if}
	</button>
{:else}
	<!-- The rail unfolding is drawn, not tweened (his 08-02 read: "TUI
	     interfaces could be drawn in a stop motion animation with some little
	     glitching"). `glitchReveal` snaps the reveal onto disagreeing frames,
	     so the rail assembles in visible steps on the way up. Only `in:` — a
	     stop-motion *disappearance* reads as failure, so condensing is a clean
	     cut (transitions.ts says so in as many words), and a one-sided
	     transition also means the two forms never coexist and fight for
	     height. -->
	<div class="panel" in:glitchReveal={{ duration: 260 }}>
		{#if condensed}
			<!-- Scrolled but pinned open: the way back down is one tap. -->
			<div
				data-measure="fold-bar"
				class="flex justify-end border-b border-stone-800/70 px-2.5 py-1"
			>
				<button
					type="button"
					class="cursor-pointer font-mono text-[9px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
					onclick={() => (pinnedOpen = false)}>▴ fold rail</button
				>
			</div>
		{/if}
		<button
			type="button"
			class="panel--pressable group block w-full text-left"
			aria-expanded={expanded}
			aria-label={expanded ? 'fold the rack' : 'expand the rack'}
			onclick={() => (expanded = !expanded)}
		>
			<!-- The whole surface opens the rack now (2026-08-03, the rack
			     answers everywhere): fuel and the tank line used to sit inert
			     beside the one pressable block on the left, so "press to see
			     more" only covered about a third of the header. Merging the
			     three regions into one button is also the modest restructure
			     the ask invited, and it is safe because none of them carries
			     an interactive child — every chip and row here is a span/div.
			     The expanded body below *does* have real buttons (repo/env/
			     core rows) and stays a separate sibling for exactly that
			     reason: the full-bleed press target belongs to the header,
			     never to a form with its own controls. -->
			<div class="grid md:grid-cols-[minmax(13rem,0.9fr)_minmax(0,1.1fr)]">
				<div
					data-measure="next-pick"
					class="min-w-0 border-b border-stone-800/70 p-2.5 md:border-r md:border-b-0"
				>
					<div
						class="mb-1 flex items-center justify-between gap-2 font-mono text-[9px] tracking-[0.13em] text-ink-quiet uppercase"
					>
						<span>next pick</span>
						<span class="flex items-center gap-1.5">
							{#if expanded && condensed}
								<!-- Invisible sticky state, named (2026-08-08, his steer: "the
								     rack block was just never collapsing after a few scrolls
								     and random presses"). The rack's own `open` rightly
								     outranks the scroll verdict forever (#1011, THE PICKER YOU
								     CANNOT REACH) — but a tap that opened it left no trace, so
								     a reader who scrolled through it never learned *why*
								     scrolling stopped doing anything. This chip is the trace:
								     same badge grammar the rack's other chips already wear. -->
								<span
									class="border border-amber-800/60 bg-amber-950/40 px-1.5 py-0.5 text-[8px] text-amber-300 normal-case"
									title="you opened this — scrolling won't fold it until you tap it closed"
									>pinned open</span
								>
							{/if}
							<span class="text-ink-mute group-hover:text-stone-400" aria-hidden="true"
								>{expanded ? '▾' : '▸'} rack</span
							>
						</span>
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
										? `${SELECTED_ACTIVE} text-amber-100`
										: 'border-stone-800/60 bg-stone-950/30 text-ink-quiet opacity-55'}"
								>
									<span class="block truncate text-[11px] font-medium">{block.profile.name}</span>
									<span
										class="mt-0.5 block truncate text-[8px] tracking-[0.11em] uppercase {block.kind ===
										'default'
											? 'text-sky-300'
											: 'text-amber-300'}">{block.badge} ▾</span
									>
								</span>
							{/each}
						</div>
					{/if}
				</div>

				<div data-measure="fuel" class="min-w-0 p-2.5" aria-label="quota fuel">
					<div
						class="mb-1 flex items-baseline justify-between gap-2 font-mono text-[9px] tracking-[0.13em] text-ink-quiet uppercase"
					>
						<span>fuel</span>
						{#if slots}
							<!-- Spawn slots as a capacity chip (#972): how much more can pass
					     through the machine right now. Neutral until ≥80% utilization,
					     then it speaks the quota vocabulary like any draining window. -->
							<span
								title={slots.title}
								class="normal-case"
								style={slots.level ? `color: ${LEVEL_COLOR[slots.level]}` : ''}>{slots.label}</span
							>
						{/if}
					</div>
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
										class="mb-0.5 flex items-baseline justify-between gap-1 font-mono text-[9px] {row.stale ||
										row.daemonStale
											? 'text-ink-mute'
											: 'text-stone-400'}"
									>
										<span class="truncate">{row.label}</span>
										<span class="flex items-center gap-1">
											{#if row.timeRemaining !== null}
												<!-- The window's own clock, drawn as one: a disc
										     that drains as the window runs down and snaps
										     back to full at reset. It fills nothing — a
										     reserve of time empties, and the fuel bar beside
										     it already reads "what is left"; a filling wedge
										     borrowed the progress-bar idiom and pointed the
										     opposite way. -->
												<svg
													viewBox="0 0 12 12"
													class="h-[9px] w-[9px] rotate-90 scale-x-[-1] {row.stale ||
													row.daemonStale
														? 'opacity-40'
														: ''}"
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
														stroke-dasharray={dialDasharray(row.timeRemaining)}
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
											class="h-full transition-[width] duration-500 ease-out {row.stale ||
											row.daemonStale
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
					data-measure="tank"
					class="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-stone-800/70 px-2.5 py-2 font-mono text-[10px] {lead.daemonStale
						? 'opacity-60'
						: ''}"
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
					{#if lead.daemonStale}
						<!-- #1503: this window only leads because nothing fresher was
						     available — `readTanks` never lets it win over a live
						     alternative, so surfacing here means every candidate was
						     equally stale (or this is the only shell reporting at
						     all). -->
						<span
							class="text-ink-mute"
							title="this shell's own daemon report is outdated — no fresher window was available to lead instead"
							>· stale daemon report</span
						>
					{/if}
				</div>
			{/if}
		</button>

		{#if expanded}
			<div class="border-t border-stone-800/70 p-3" in:glitchReveal={{ duration: 240, delay: 40 }}>
				<!-- Action receipts live with the control that caused them; keeping
			     them in the expanded rack avoids turning the glance strip into a
			     transient status-message row. -->
				<div data-measure="error-note">
					{#if runnersError}
						<p class="mb-2 text-sm text-red-400">{runnersError}</p>
					{/if}
					{#if runnersNote}
						<p class="mb-2 font-mono text-xs text-amber-300">{runnersNote}</p>
					{/if}
				</div>
				{#if runners === null}
					{#if !runnersError}
						<p class="text-sm text-ink-quiet">Loading…</p>
					{/if}
				{:else}
					<div class="mb-3 grid gap-3 lg:grid-cols-2">
						<div data-measure="project" class="panel p-4">
							<div
								class="mb-3 font-mono text-sm font-medium tracking-wide text-amber-200 uppercase"
							>
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
												? `next pick → ${repo.repo_full_name}`
												: `daemon ${repo.daemon_status} — cannot take a pick`}
											onclick={() => selectRepo(repo)}
											class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {dispatchable
												? selected
													? SELECTED_OPTION
													: IDLE_ROW
												: DISABLED_ROW}"
										>
											<span
												class="truncate font-mono text-xs {!dispatchable
													? 'text-ink-mute'
													: selected
														? 'text-amber-200'
														: 'text-stone-300'}"
											>
												{dispatchable ? '' : UNAVAILABLE_MARK}{repo.repo_full_name}
											</span>
											<span
												class="flex shrink-0 items-baseline gap-2 font-mono text-[10px] uppercase"
											>
												{#if repo.dispatch_default}<span class="text-sky-300">default</span>{/if}
												<span
													class={repo.daemon_status === 'online'
														? 'text-stone-400'
														: 'text-ink-mute'}
												>
													{repo.daemon_status}
												</span>
											</span>
										</button>
									{/each}
								</div>
							{/if}
						</div>

						<div data-measure="environment" class="panel p-4">
							<div
								class="mb-3 font-mono text-sm font-medium tracking-wide text-amber-200 uppercase"
							>
								environment
							</div>
							<div class="space-y-1.5">
								<button
									type="button"
									onclick={() => (environmentSelection = null)}
									class="flex w-full items-baseline justify-between gap-3 border px-2 py-1.5 text-left transition-colors {environmentSelection ===
									null
										? SELECTED_OPTION
										: IDLE_ROW}"
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
												? SELECTED_OPTION
												: IDLE_ROW
											: DISABLED_ROW}"
									>
										<span
											class="font-mono text-xs {option.available
												? 'text-stone-300'
												: 'text-ink-mute'}"
										>
											{option.available ? '' : UNAVAILABLE_MARK}{option.name}
										</span>
										{#if !option.available}
											<span class="truncate font-mono text-[10px] text-ink-mute"
												>{option.reason}</span
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
						sticky={runners.sticky ?? null}
						{now}
						onTap={tapRunner}
						{onReleaseSticky}
					/>
				{/if}
			</div>
		{/if}
	</div>
{/if}
