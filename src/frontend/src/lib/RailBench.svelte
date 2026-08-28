<script lang="ts">
	import { glitchReveal } from './transitions';
	import { environmentDisplay } from './railBench';
	import type { ConnectedRepo, EnvironmentOption } from './repos';
	import { IDLE_ROW, OFF_MARK, OFF_ROW, SELECTED_OPTION } from './stateChrome';

	// THE BENCH — project · environment, and nothing else, as its own block
	// at the top of the page.
	//
	// This was THE BENCH: project + environment + a provider's Resources + a
	// `CLAUDE | CODEX` core picker, four things under one heading. The
	// provider half moved out to `ProviderBay` (2026-08-28), opened by
	// pressing that provider's fuel row. What is left here is the pair that
	// is true regardless of which body runs next: *where* the work happens.
	//
	// The move that produced this shape (maintainer, 2026-08-28: "we need a
	// bench/settings whatever block, collapsed, on the very top of the page,
	// above the fuel, stating the settings, and expandable on press"): the
	// handle used to be a `▸ settings` button on the gauge's own footline
	// while the body mounted *below* the provider bay — so pressing a fuel
	// row inserted a whole panel between a control and the thing it opened,
	// and the two read as unrelated surfaces. **A disclosure's handle and its
	// body are one object**; this component now owns both, mounts above the
	// fuel rail, and grows downward from its own line.
	//
	// Collapsed it is not a bare label: it *states* the pair it governs, so
	// the common case — "where does the next wake land" — is answered without
	// a press, and the press is only for changing the answer.
	//
	// It also mounts unconditionally now, open or shut. While it was
	// `{#if settingsOpen}`, closing it unmounted the component and reset
	// `repoSelection`/`environmentSelection` to null — so a reader who picked
	// a project, folded the block, and reopened it saw the *default* selected
	// while the page still held the announced pick. The selection outliving
	// the fold is not a feature added here; it is the drift removed.
	interface Props {
		repos?: ConnectedRepo[] | null;
		/** Whether the picking surface is unfolded. Owned by the page, because
		 *  opening it also scrolls the reader back to the top. */
		open: boolean;
		onToggle: () => void;
		/** Raised whenever the reader changes *where* the next wake runs, so
		 *  the page can hand the pair to whichever core row is tapped in the
		 *  provider bay — which lives outside this component now. */
		onPlaceChange?: (repoLabel: string | null, environment: string | null) => void;
		/** The repo label the daemon says the next wake already targets, used
		 *  only as the initial reading before the reader has picked anything. */
		wakeRepoLabel?: string | null;
	}

	let { repos = null, open, onToggle, onPlaceChange, wakeRepoLabel = null }: Props = $props();

	let repoSelection = $state<string | null>(null);
	let environmentSelection = $state<string | null>(null);
	let selectedRepo = $derived(
		(repos ?? []).find((repo) => repo.repo_full_name === (repoSelection ?? wakeRepoLabel)) ??
			(repos ?? []).find((repo) => repo.dispatch_default) ??
			(repos ?? [])[0]
	);
	let environmentOptions = $derived<EnvironmentOption[]>(selectedRepo?.environments ?? []);
	let environment = $derived(environmentDisplay(selectedRepo, environmentSelection));

	/** The collapsed line's project reading. `null` repos is genuinely "not
	 *  known yet" and says so — an empty account says something different
	 *  again, and neither may borrow the other's wording. */
	let projectReading = $derived(
		repos === null ? 'loading…' : (selectedRepo?.repo_full_name ?? 'no project connected')
	);

	/** Every fact the folded line compresses, in words — for a reader on a
	 *  narrow screen where the line itself has truncated. */
	let handleTitle = $derived(
		`where the next wake lands — project: ${projectReading} · environment: ${environment.name}${
			environment.isDefault ? ' (repo default)' : ''
		}`
	);

	function selectRepo(repo: ConnectedRepo) {
		repoSelection = repo.repo_full_name;
		environmentSelection = null;
		announce();
	}

	function selectEnvironment(name: string | null) {
		environmentSelection = name;
		announce();
	}

	// The place a wake lands is chosen here and *used* one component over, on
	// whichever core row gets tapped. Announcing on change rather than
	// reaching across keeps that a one-way flow: this block never learns what
	// the rack does with the pair.
	function announce() {
		onPlaceChange?.(selectedRepo?.repo_full_name ?? null, environmentSelection);
	}
</script>

<div data-measure="settings" class="settings-block bg-stone-950">
	<button
		type="button"
		data-role="bench-handle"
		aria-expanded={open}
		aria-label={open ? 'fold the bench' : 'open the bench — project and environment'}
		title={handleTitle}
		onclick={onToggle}
		class="bench-handle"
		class:is-open={open}
	>
		<span class="bench-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
		<span class="bench-title">bench</span>
		<!-- Stating the settings is the whole point of the folded form: a
		     handle that only said "settings" made the reader open it to learn
		     the one thing they usually wanted. -->
		<span class="bench-state">
			<span class="bench-key">project</span>
			<span class="bench-value">{projectReading}</span>
			<span class="bench-sep" aria-hidden="true">·</span>
			<span class="bench-key">env</span>
			<span class="bench-value">{environment.name}</span>
		</span>
	</button>

	{#if open}
		<div class="bench-body px-2 py-3 sm:px-5 sm:py-4" in:glitchReveal={{ duration: 240 }}>
			<div class="bench-bays grid gap-3 md:grid-cols-2 md:gap-5">
				<section data-measure="project" class="bench-bay">
					<div class="workshop-label">project</div>
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
								     will serve (2026-07-22 round). Same off-row grammar as the
								     environment options below — design it off, don't dim it. -->
								<button
									data-role="bench-pick"
									type="button"
									disabled={!dispatchable}
									title={dispatchable
										? `next pick → ${repo.repo_full_name}`
										: `daemon ${repo.daemon_status} — cannot take a pick`}
									onclick={() => selectRepo(repo)}
									class="pick-row flex min-h-11 w-full items-center justify-between gap-4 border px-3 py-2 text-left transition-colors {dispatchable
										? selected
											? SELECTED_OPTION
											: IDLE_ROW
										: OFF_ROW}"
								>
									<span
										class="min-w-0 truncate font-mono text-sm font-medium {!dispatchable
											? 'text-ink-mute'
											: selected
												? 'text-stone-100'
												: 'text-stone-300'}"
									>
										{dispatchable ? '' : OFF_MARK}{repo.repo_full_name}
									</span>
									<span
										class="flex shrink-0 flex-col items-end font-mono text-[10px] leading-tight uppercase sm:flex-row sm:gap-2"
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
				</section>

				<section data-measure="environment" class="bench-bay">
					<div class="workshop-label">environment</div>
					<div class="space-y-1.5">
						<button
							data-role="bench-pick"
							type="button"
							onclick={() => selectEnvironment(null)}
							class="pick-row flex min-h-11 w-full items-center justify-between gap-4 border px-3 py-2 text-left transition-colors {environmentSelection ===
							null
								? SELECTED_OPTION
								: IDLE_ROW}"
						>
							<!-- #1516: the name and the badge render as two elements now,
							     never one string joined by the same `·` the name may
							     already carry internally (`host · default` is a real
							     environment name). -->
							<span class="font-mono text-sm font-medium text-stone-100">{environment.name}</span>
							<span
								class="flex shrink-0 flex-col items-end font-mono text-[10px] leading-tight uppercase sm:flex-row sm:gap-2"
							>
								{#if environment.isDefault}<span class="text-sky-300">default</span>{/if}
								<span class="text-ink-quiet">from repo policy</span>
							</span>
						</button>
						{#each environmentOptions as option (option.name)}
							<button
								data-role="bench-pick"
								type="button"
								disabled={!option.available}
								title={option.reason ?? `next wake in ${option.name}`}
								onclick={() => selectEnvironment(option.name)}
								class="pick-row flex min-h-11 w-full items-center justify-between gap-4 border px-3 py-2 text-left transition-colors {option.available
									? environmentSelection === option.name
										? SELECTED_OPTION
										: IDLE_ROW
									: OFF_ROW}"
							>
								<span
									class="font-mono text-sm font-medium {option.available
										? 'text-stone-300'
										: 'text-ink-mute'}"
								>
									{option.available ? '' : OFF_MARK}{option.name}
								</span>
								{#if !option.available}
									<span class="truncate font-mono text-[10px] text-ink-mute">{option.reason}</span>
								{/if}
							</button>
						{/each}
						{#if environmentOptions.length === 0}
							<p class="px-2 font-mono text-[10px] text-ink-mute">No daemon availability report.</p>
						{/if}
					</div>
				</section>
			</div>
		</div>
	{/if}
</div>

<style>
	/* Hairline, not a box. Folded, this is one 32px line directly above the
	   rail — a full border made it read as a different species of object than
	   the gauge it introduces, and the workshop grid (designed for a tall
	   panel) rendered across it as a row of noise squares rather than
	   texture. The grid lives on the body, where there is room for it to be
	   a ground instead of a pattern. */
	.settings-block {
		border-top: 1px solid rgb(68 64 60 / 0.55);
		border-bottom: 1px solid rgb(68 64 60 / 0.55);
	}
	/* One line, the gauge's own register (mono, 9-10px, uppercase keys), so
	   the two blocks read as instruments off the same panel rather than a
	   control bolted above one. */
	.bench-handle {
		display: flex;
		align-items: baseline;
		gap: 7px;
		width: 100%;
		min-height: 30px;
		min-width: 0;
		border: 0;
		background: none;
		padding: 7px 10px;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 9px;
		text-align: left;
		color: rgb(168 162 158);
		cursor: pointer;
	}
	.bench-caret {
		flex: none;
		width: 7px;
		font-size: 8px;
		color: rgb(120 113 108);
	}
	.bench-handle.is-open .bench-caret,
	.bench-handle:hover .bench-caret {
		color: rgb(214 211 209);
	}
	.bench-title {
		flex: none;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: rgb(214 211 209);
	}
	.bench-state {
		display: flex;
		align-items: baseline;
		gap: 5px;
		min-width: 0;
		overflow: hidden;
		white-space: nowrap;
	}
	.bench-key {
		flex: none;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: rgb(120 113 108);
	}
	.bench-value {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		font-size: 10px;
		color: rgb(214 211 209);
	}
	.bench-sep {
		flex: none;
		color: rgb(68 64 60);
	}
	.bench-body {
		border-top: 1px solid rgb(68 64 60 / 0.55);
		background-image:
			linear-gradient(rgb(255 255 255 / 0.025) 1px, transparent 1px),
			linear-gradient(90deg, rgb(255 255 255 / 0.018) 1px, transparent 1px);
		background-size: 24px 24px;
	}
	.bench-bay {
		min-width: 0;
	}
	.workshop-label {
		margin-bottom: 0.4rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: rgb(168 162 158);
	}
	.pick-row {
		min-height: 44px;
	}
</style>
