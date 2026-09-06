<script lang="ts">
	import type { WithheldLane } from '$lib/withheld';
	import type { Snippet } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		commandQueue,
		missionRun,
		unlockCount,
		parseDeckCommand,
		type DeckSection
	} from './commandDeck';
	import { goalItems, type WarpGraph } from './warpGraph';
	import { liveRunDisplayName, runCourse, type LiveRun } from './liveRuns';
	import type { QuotaShell } from './quota';
	import { fuelRows } from './railGauge';
	import WinkWordmark from './WinkWordmark.svelte';
	import AsciiField from './AsciiField.svelte';
	import ResidentField from './ResidentField.svelte';

	interface Props {
		section: DeckSection;
		runs: LiveRun[] | null;
		graph: WarpGraph;
		shells: QuotaShell[] | null;
		loading: boolean;
		error: string | null;
		stale: boolean;
		withheld: WithheldLane | null;
		now: number;
		map: boolean;
		sceneVisible: boolean;
		onSection: (section: DeckSection) => void;
		onRun: (id: string) => void;
		onPage: (path: string) => void;
		onMap: (runId: string | null) => void;
		onInstruments: () => void;
		setup: Snippet;
		attention: Snippet;
		content: Snippet;
	}
	let {
		section,
		runs,
		graph,
		shells,
		loading,
		error,
		stale,
		withheld,
		now,
		map,
		sceneVisible,
		onSection,
		onRun,
		onPage,
		onMap,
		onInstruments,
		setup,
		attention,
		content
	}: Props = $props();
	let command = $state('');
	let commandReply = $state('Try look, inspect, map, or open w-1. Type help for every command.');
	let commandInput = $state<HTMLInputElement | null>(null);
	let fuel = $derived(fuelRows(shells ?? [], now));
	function execute() {
		const action = parseDeckCommand(command);
		if (action.kind === 'section') {
			onSection(action.section);
			commandReply = `Opened ${command.trim()}.`;
		} else if (action.kind === 'map') {
			onMap(mission?.run_id ?? null);
			commandReply = 'Map expanded. Escape returns to the bridge.';
		} else if (action.kind === 'inspect') {
			if (mission) {
				onRun(mission.id);
				commandReply = 'Run opened. Escape returns to the bridge.';
			} else commandReply = 'No active run to inspect.';
		} else if (action.kind === 'look') {
			commandReply = mission?.card_text ?? 'No current mission note is available.';
		} else if (action.kind === 'open') {
			const item = graph.itemById.get(action.id);
			if (item) {
				onPage(item.path);
				commandReply = `Opened ${item.id}: ${item.headline}`;
			} else commandReply = `No item named ${action.id} in the current report.`;
		} else if (action.kind === 'crew') {
			const run = runs?.[action.index];
			if (run) {
				selectedId = run.id;
				onSection('overview');
				commandReply = `Selected ${liveRunDisplayName(run)}.`;
			} else commandReply = 'That crew number is not present. Choose a numbered crew member.';
		} else if (action.kind === 'help')
			commandReply =
				'look · inspect · map · crew <number> · open <item-id> · bridge · objectives · log · knowledge · artifacts. These navigate the workspace. Send new work through your connected chat.';
		else commandReply = 'Unknown command. Type help to see the available verbs.';
		command = '';
	}
	function shortcut(event: KeyboardEvent) {
		if (
			event.key !== '/' ||
			document.querySelector('[role="dialog"]') ||
			event.ctrlKey ||
			event.metaKey ||
			event.altKey ||
			event.target instanceof HTMLInputElement ||
			event.target instanceof HTMLTextAreaElement ||
			(event.target instanceof HTMLElement && event.target.isContentEditable)
		)
			return;
		event.preventDefault();
		commandInput?.focus();
	}
	let selectedId = $state<string | null>(null);
	let mission = $derived(missionRun(runs ?? [], selectedId));
	let course = $derived(mission ? runCourse(mission.card_text, mission.course) : null);
	let decisions = $derived(commandQueue(graph, runs ?? []));
	let goals = $derived(goalItems(graph));
	const sections: { id: DeckSection; icon: string; label: string }[] = [
		{ id: 'overview', icon: '⌂', label: 'Command' },
		{ id: 'objectives', icon: '◇', label: 'Objectives' },
		{ id: 'history', icon: '↗', label: 'Ship log' },
		{ id: 'library', icon: '▤', label: 'Knowledge' },
		{ id: 'shelf', icon: '▱', label: 'Artifacts' }
	];
</script>

<svelte:window onkeydown={shortcut} />

<div class="command-deck">
	<aside class="deck-nav">
		<a class="brand" href={resolve('/')} aria-label="brnrd home"><WinkWordmark /></a>
		<p class="nav-caption">YOUR REPO. YOUR CREW.</p>
		<nav aria-label="Workspace">
			{#each sections as item (item.id)}
				<button
					class:chosen={section === item.id}
					aria-current={section === item.id ? 'page' : undefined}
					onclick={() => onSection(item.id)}
					><span aria-hidden="true">{item.icon}</span>{item.label}
				</button>
			{/each}
		</nav>
		<div class="nav-bottom">
			<button onclick={onInstruments}>⚙ Instruments</button>
			<a href={resolve('/repos')}>Manage repos ↗</a>
			<a href="/logout" rel="external">Sign out</a>
		</div>
	</aside>

	<main class="deck-main">
		<header class="deck-header">
			<div>
				<p class="overline">WORKSPACE / {sections.find((item) => item.id === section)?.label}</p>
				<h1>
					{section === 'overview'
						? 'You have the bridge.'
						: sections.find((item) => item.id === section)?.label}
				</h1>
			</div>
			<button class="instrument-link" onclick={onInstruments}>Resources & controls ↗</button>
		</header>
		{@render setup()}
		{#if error}<p class="deck-error" role="status">
				{error} · report unavailable; any retained values may be outdated.
			</p>{/if}
		{#if stale}<p class="deck-error" role="status">
				Live report is stale. Inspect the timestamp before acting.
			</p>{/if}
		<div class="resource-strip" aria-label="Provider quota">
			<span class="overline">FUEL</span>
			{#if shells === null}<span>Reading resources…</span>
			{:else if shells.length === 0}<span>No quota readings available</span>
			{:else}
				{#each fuel as row (row.id)}
					<button onclick={onInstruments} class="fuel-cell" title={row.tooltip}>
						<strong>{row.label}</strong><b
							>{row.percentLabel}{row.percent !== null ? ' left' : ''}</b
						>
						{#if row.stale || row.daemonStale}<span>stale</span>{/if}
						{#if row.resetShort}<span>↻ {row.resetShort}</span>{/if}
					</button>
				{/each}
			{/if}
		</div>
		{#if section === 'overview'}
			<div class="bridge-grid">
				<div class="mission-column">
					<section class="mission-panel" aria-labelledby="mission-heading">
						<div class="panel-top">
							<span class="overline">01 / LIVE MISSION</span><span class="live-status"
								>{runs === null
									? error
										? 'UNAVAILABLE'
										: 'CONNECTING'
									: mission
										? (mission.lifecycle ?? mission.phase ?? 'reported').toUpperCase()
										: withheld
											? 'WITHHELD'
											: 'BETWEEN MISSIONS'}</span
							>
						</div>
						{#if mission}
							<p class="repo-name">
								{mission.repo_label} <span>/ {mission.room?.branch ?? 'branch not reported'}</span>
							</p>
							<h2 id="mission-heading">{liveRunDisplayName(mission)}</h2>
							<p class="mission-intent">
								{course?.current ??
									mission.card_text ??
									'The resident has not written a current objective yet.'}
							</p>
							<div class="mission-foot">
								{#if course && course.total > 0}<div class="course">
										<span>{course.done} / {course.total} steps complete</span><progress
											max={course.total}
											value={course.done}
											aria-label="Mission steps complete"
										></progress>
									</div>{/if}
								<button class="primary" onclick={() => onRun(mission!.id)}>Inspect & steer ↗</button
								>
							</div>
						{:else}
							<h2 id="mission-heading">
								{withheld
									? 'The live view is withheld.'
									: error
										? 'The live report is unavailable.'
										: runs === null
											? 'Reaching your resident…'
											: 'A quiet bridge. A ready repo.'}
							</h2>
							<p class="mission-intent">
								{withheld
									? 'Review the publish scope in your repo settings to see what can be shared here.'
									: error
										? 'The next successful report will restore the view.'
										: runs === null
											? 'Waiting for the live report.'
											: 'Send the next objective through your connected chat. Your resident picks up the project context.'}
							</p>
							<a class="primary" href={resolve('/repos')}>Open chat connections ↗</a>
						{/if}
					</section>

					<section class="world-panel" aria-label="Live workspace scene">
						<div class="panel-top">
							<span class="overline">THE FIELD / {map ? 'MAP' : 'CREW VIEW'}</span><button
								onclick={() => onMap(mission?.run_id ?? null)}>Expand map ⤢</button
							>
						</div>
						{#if !sceneVisible}<p class="empty-copy">The field is open in the expanded view.</p>
						{:else if map}<AsciiField
								focusRunId={mission?.run_id ?? null}
								compact
								rows={18}
								header={false}
								legendDefault={false}
							/>
						{:else if runs && runs.length > 0}<ResidentField
								{runs}
								{stale}
								{now}
								onSelect={onRun}
								{selectedId}
							/>
						{:else}<div class="quiet-field">
								<span aria-hidden="true">⌂</span>
								<p>The workspace is still.</p>
								<a href={resolve('/ascii')}>Explore the map →</a>
							</div>{/if}
						<div class="world-caption">
							<span>Every movement comes from a reported action.</span><a
								href={map ? resolve('/') : resolve('/daily')}>{map ? 'Crew view' : 'Map view'} →</a
							>
						</div>
					</section>
				</div>

				<aside class="crew-column">
					<section class="crew-panel" aria-labelledby="crew-heading">
						<div class="panel-top">
							<h2 id="crew-heading" class="overline">02 / YOUR CREW</h2>
							<span class="count">{runs?.length ?? '—'}</span>
						</div>
						{#each runs ?? [] as run, i (run.id)}
							<button
								class="crew-member"
								class:selected={mission?.id === run.id}
								onclick={() => (selectedId = run.id)}
							>
								<span class="face"
									><small>{i + 1}</small>{run.mood_rest ?? run.mood_glyph ?? 'b·_·d'}</span
								>
								<span
									><b>{liveRunDisplayName(run)}</b><small
										>{run.is_subspawn ? 'Worker' : 'Resident'} · {run.runner.model_observed ??
											run.runner.core ??
											run.runner.shell ??
											'runner unreported'}</small
									><small>{run.lifecycle ?? run.phase ?? 'state unreported'}</small></span
								>
							</button>
						{:else}<p class="empty-copy">
								{withheld
									? 'Live crew data is withheld.'
									: error
										? 'Crew report unavailable.'
										: runs === null
											? 'Loading crew…'
											: 'No active runs reported.'}
							</p>{/each}
					</section>
					<section class="goal-panel" aria-labelledby="goal-heading">
						<h2 id="goal-heading" class="overline">THE BIGGER PICTURE</h2>
						{#each goals.slice(0, 2) as goal (goal.id)}
							<button class="goal" onclick={() => onPage(goal.path)}
								><span class="goal-symbol" aria-hidden="true">◎</span><b>{goal.headline}</b><small
									>{goal.target ?? 'Open goal'}{goal.horizon ? ` · ${goal.horizon}` : ''}</small
								><span>Read objective →</span></button
							>
						{:else}<p class="empty-copy">
								{loading
									? 'Reading objectives…'
									: 'No open goal is recorded. The next one starts in your conversation.'}
							</p>{/each}
					</section>
				</aside>
			</div>

			<section class="decision-panel" aria-labelledby="decision-heading">
				<div class="panel-top">
					<div>
						<p class="overline">03 / YOUR NEXT MOVES</p>
						<h2 id="decision-heading">Decisions that move the work.</h2>
					</div>
					<button onclick={() => onSection('objectives')}
						>All objectives <span class="count"
							>{loading ? '—' : graph.items.filter((item) => item.state === 'open').length}</span
						> →</button
					>
				</div>
				{@render attention()}
				{#if loading}<p class="empty-copy">Reading the work graph…</p>
				{:else if decisions.length === 0}<p class="empty-copy">
						No unblocked decisions or preparations in the current report.
					</p>
				{:else}
					<p class="queue-note">
						Showing {Math.min(3, decisions.length)} of {decisions.length} · most direct dependencies first
					</p>
					{#each decisions.slice(0, 3) as item, i (item.id)}
						<button class="decision" onclick={() => onPage(item.path)}
							><span class="decision-number">0{i + 1}</span><span class="decision-copy"
								><b>{item.headline}</b><small>{item.id} / {item.type}</small></span
							><span class="dependency"
								>{unlockCount(item.id, graph)
									? `${unlockCount(item.id, graph)} depend on this`
									: 'Ready to consider'}<span aria-hidden="true"> ↗</span></span
							></button
						>
					{/each}
				{/if}
			</section>
		{:else}
			<section
				class="deck-content"
				aria-label={sections.find((item) => item.id === section)?.label}
			>
				{@render content()}
			</section>
		{/if}
		<div class="command-console">
			<form
				onsubmit={(event) => {
					event.preventDefault();
					execute();
				}}
			>
				<label for="bridge-command">COMMAND <span aria-hidden="true">›</span></label>
				<input
					id="bridge-command"
					bind:this={commandInput}
					bind:value={command}
					placeholder="look, inspect, open w-1…"
					autocomplete="off"
					spellcheck="false"
				/>
				<button type="submit">Execute ↵</button>
			</form>
			<p role="status">{commandReply}</p>
		</div>
		<footer class="deck-footer">
			<span>LOCAL EXECUTION. LASTING MEMORY. REAL RECEIPTS.</span><a href={resolve('/repos')}
				>Connect a conversation ↗</a
			>
		</footer>
	</main>
</div>

<style>
	.command-console {
		position: sticky;
		bottom: 12px;
		z-index: 30;
		background: #19160f;
		border: 1px solid #73613b;
		margin-top: 24px;
		box-shadow: 0 6px 30px #0008;
	}
	.command-console form {
		display: flex;
		gap: 12px;
		align-items: center;
		padding: 14px;
	}
	.command-console label {
		font-size: 9px;
		color: #e2c477;
		white-space: nowrap;
	}
	.command-console label span {
		margin-left: 10px;
		font-size: 20px;
	}
	.command-console input {
		width: 100%;
		min-width: 0;
		color: #f6e8cc;
		font: 12px monospace;
		background: transparent;
		outline-offset: 4px;
	}
	.command-console button {
		font-size: 10px;
		white-space: nowrap;
		color: #edcd83;
	}
	.command-console p {
		max-height: 100px;
		overflow: auto;
		border-top: 1px solid #393122;
		padding: 9px 14px;
		font: 11px/1.6 monospace;
		color: #b8ac94;
		white-space: pre-line;
	}
	.command-deck {
		--deck-line: #38332a;
		--deck-quiet: #b4aa98;
		display: grid;
		grid-template-columns: 176px minmax(0, 1fr);
		max-width: 1600px;
		margin: auto;
		min-height: 100vh;
		color: #eee6d5;
		font-family: var(--font-mono, monospace);
	}
	.deck-nav {
		padding: 34px 20px;
		border-right: 1px solid var(--deck-line);
		display: flex;
		flex-direction: column;
		position: sticky;
		top: 0;
		height: 100dvh;
	}
	.brand {
		font-size: 30px;
		color: #f9d885;
		font-weight: 700;
	}
	.nav-caption {
		font-size: 8px;
		letter-spacing: 0.12em;
		color: var(--deck-quiet);
		margin: 10px 0 42px;
	}
	nav {
		display: grid;
		gap: 8px;
	}
	nav button {
		display: flex;
		gap: 12px;
		align-items: center;
		padding: 13px 10px;
		text-align: left;
		font-size: 12px;
		border-left: 2px solid transparent;
		color: var(--deck-quiet);
	}
	nav button span {
		font-size: 19px;
	}
	nav button.chosen {
		background: #e9bc5720;
		border-color: #ecc56b;
		color: #fce1a1;
	}
	button,
	a {
		cursor: pointer;
	}
	button:hover,
	a:hover {
		color: #ffe3a0;
	}
	button:focus-visible,
	a:focus-visible {
		outline: 2px solid #ead094;
		outline-offset: 4px;
	}
	.nav-bottom {
		margin-top: auto;
		padding-top: 40px;
		display: grid;
		gap: 20px;
		font-size: 11px;
		color: var(--deck-quiet);
	}
	.nav-bottom button {
		text-align: left;
	}
	.deck-main {
		padding: 34px 34px 20px;
		min-width: 0;
	}
	.deck-header {
		display: flex;
		justify-content: space-between;
		gap: 20px;
		align-items: center;
		margin-bottom: 25px;
	}
	.overline {
		font-size: 10px;
		letter-spacing: 0.12em;
		color: var(--deck-quiet);
		font-weight: 500;
	}
	h1 {
		font-family: system-ui, sans-serif;
		font-size: clamp(28px, 3vw, 42px);
		font-weight: 600;
		letter-spacing: -0.045em;
		margin: 8px 0 0;
		line-height: 1.15;
	}
	.instrument-link {
		font-size: 10px;
		color: var(--deck-quiet);
	}
	.resource-strip {
		display: flex;
		flex-wrap: wrap;
		gap: 18px;
		align-items: center;
		padding: 12px 0;
		border-block: 1px solid var(--deck-line);
		margin-bottom: 28px;
		font-size: 10px;
		color: var(--deck-quiet);
	}
	.fuel-cell {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
		align-items: center;
	}
	.fuel-cell strong {
		color: #eee6d5;
	}
	.fuel-cell b {
		color: #9ec7b8;
		font-weight: 500;
	}
	.bridge-grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 270px;
		gap: 22px;
	}
	.mission-column,
	.crew-column {
		min-width: 0;
	}
	.mission-panel {
		border: 1px solid #8d713e;
		background: radial-gradient(ellipse at top left, #c8952b1a, transparent 80%), #16130e;
		padding: 22px;
	}
	.panel-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.panel-top button {
		font-size: 10px;
		color: #d8c8a6;
	}
	.live-status {
		color: #acd6c5;
		font-size: 9px;
		letter-spacing: 0.08em;
	}
	.repo-name {
		color: #d9be7c;
		font-size: 10px;
		margin-top: 23px;
		overflow-wrap: anywhere;
	}
	.repo-name span {
		color: var(--deck-quiet);
	}
	.mission-panel h2 {
		font:
			600 clamp(22px, 2.4vw, 31px)/1.2 system-ui,
			sans-serif;
		letter-spacing: -0.025em;
		margin-top: 10px;
		overflow-wrap: anywhere;
	}
	.mission-intent {
		color: #c6bdad;
		font:
			14px/1.65 system-ui,
			sans-serif;
		margin: 15px 0 24px;
		white-space: pre-line;
		overflow-wrap: anywhere;
	}
	.mission-foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 18px;
		flex-wrap: wrap;
	}
	.primary {
		display: inline-block;
		padding: 12px 16px;
		background: #ecc56b;
		border: 1px solid #f5d991;
		color: #1c1609;
		font-size: 11px;
		font-weight: 700;
	}
	.primary:hover {
		background: #ffe0a0;
		color: #1c1609;
	}
	.course {
		flex: 1;
		min-width: 100px;
		font-size: 10px;
		color: var(--deck-quiet);
	}
	progress {
		display: block;
		width: 100%;
		height: 3px;
		margin-top: 9px;
		accent-color: #e8c271;
	}
	progress::-webkit-progress-bar {
		background: #373024;
	}
	progress::-webkit-progress-value {
		background: #e8c271;
	}
	.world-panel {
		margin-top: 18px;
		border: 1px solid var(--deck-line);
		overflow: hidden;
	}
	.world-panel > .panel-top {
		padding: 14px 18px;
		background: #16130e;
	}
	.world-panel :global(.ascii-field) {
		max-width: 100%;
	}
	.world-caption {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 12px 16px;
		font-size: 9px;
		color: var(--deck-quiet);
		border-top: 1px solid var(--deck-line);
	}
	.quiet-field {
		text-align: center;
		padding: 70px 20px;
		color: var(--deck-quiet);
		font-size: 12px;
	}
	.quiet-field > span {
		display: block;
		font-size: 42px;
		color: #b5985a;
		margin-bottom: 12px;
	}
	.quiet-field a {
		display: block;
		margin-top: 15px;
	}
	.crew-panel,
	.goal-panel {
		border-top: 1px solid var(--deck-line);
		padding-top: 16px;
	}
	.goal-panel {
		margin-top: 28px;
	}
	.count {
		font-size: 10px;
		color: #dac48d;
		border: 1px solid #514633;
		padding: 2px 6px;
	}
	.crew-member {
		display: flex;
		width: 100%;
		text-align: left;
		gap: 12px;
		margin-top: 12px;
		padding: 13px 10px;
		border: 1px solid transparent;
	}
	.crew-member.selected {
		border-color: #6c5735;
		background: #b9903820;
	}
	.face {
		color: #eccc82;
		font-size: 15px;
		white-space: nowrap;
	}
	.crew-member b {
		font:
			500 13px/1.35 system-ui,
			sans-serif;
		display: block;
		overflow-wrap: anywhere;
	}
	.crew-member small {
		display: block;
		margin-top: 6px;
		font-size: 9px;
		color: var(--deck-quiet);
		overflow-wrap: anywhere;
	}
	.goal {
		text-align: left;
		width: 100%;
		margin-top: 18px;
		display: grid;
		gap: 12px;
	}
	.goal-symbol {
		color: #d8bf79;
		font-size: 25px;
	}
	.goal b {
		font:
			500 16px/1.4 system-ui,
			sans-serif;
	}
	.goal small {
		font:
			12px/1.55 system-ui,
			sans-serif;
		color: var(--deck-quiet);
	}
	.goal > span:last-child {
		font-size: 10px;
		color: #e3c476;
	}
	.decision-panel {
		margin-top: 30px;
		border-top: 1px solid var(--deck-line);
		padding-top: 20px;
	}
	.decision-panel h2 {
		font:
			500 22px/1.3 system-ui,
			sans-serif;
		margin-top: 6px;
	}
	.queue-note {
		color: var(--deck-quiet);
		font-size: 9px;
		margin: 17px 0 8px;
	}
	.decision {
		display: flex;
		width: 100%;
		text-align: left;
		align-items: center;
		gap: 18px;
		padding: 17px 0;
		border-bottom: 1px solid var(--deck-line);
	}
	.decision-number {
		font-size: 22px;
		color: #96856a;
	}
	.decision-copy {
		flex: 1;
		min-width: 0;
	}
	.decision-copy b {
		display: block;
		font:
			500 15px/1.4 system-ui,
			sans-serif;
	}
	.decision-copy small {
		display: block;
		margin-top: 4px;
		font-size: 9px;
		color: var(--deck-quiet);
	}
	.dependency {
		color: #d0bb8d;
		font-size: 10px;
	}
	.empty-copy {
		color: var(--deck-quiet);
		font:
			13px/1.6 system-ui,
			sans-serif;
		margin: 20px 0;
	}
	.deck-error {
		padding: 12px;
		color: #f4ba87;
		border: 1px solid #754b2d;
		font-size: 12px;
		margin-bottom: 16px;
	}
	.deck-content {
		min-height: 60vh;
	}
	.deck-footer {
		margin-top: 40px;
		padding-top: 16px;
		border-top: 1px solid var(--deck-line);
		display: flex;
		gap: 20px;
		justify-content: space-between;
		font-size: 8px;
		letter-spacing: 0.05em;
		color: var(--deck-quiet);
	}
	@media (min-width: 1700px) {
		.command-deck {
			border-inline: 1px solid var(--deck-line);
		}
	}
	@media (max-width: 1100px) {
		.command-deck {
			grid-template-columns: 150px minmax(0, 1fr);
		}
		.deck-main {
			padding: 24px;
		}
		.bridge-grid {
			grid-template-columns: minmax(0, 1fr) 220px;
			gap: 16px;
		}
		.deck-nav {
			padding-inline: 12px;
		}
	}
	@media (max-width: 850px) {
		.command-deck {
			display: block;
		}
		.deck-nav {
			position: static;
			height: auto;
			padding: 16px 20px 0;
			border-right: 0;
		}
		.brand {
			font-size: 24px;
		}
		.nav-caption,
		.nav-bottom {
			display: none;
		}
		nav {
			display: flex;
			overflow-x: auto;
			gap: 4px;
			margin-top: 18px;
			border-bottom: 1px solid var(--deck-line);
		}
		nav button {
			flex-shrink: 0;
			border-left: 0;
			border-bottom: 2px solid transparent;
			font-size: 11px;
			padding: 12px 10px;
		}
		nav button span {
			font-size: 16px;
		}
		.deck-main {
			padding: 24px 20px;
		}
		.bridge-grid {
			grid-template-columns: minmax(0, 1fr);
		}
		.crew-column {
			display: grid;
			grid-template-columns: 1fr 1fr;
			gap: 20px;
		}
		.goal-panel {
			margin-top: 0;
		}
		.instrument-link {
			max-width: 110px;
			text-align: right;
		}
	}
	@media (max-width: 480px) {
		.deck-header {
			align-items: flex-start;
			gap: 10px;
		}
		.deck-main {
			padding: 22px 16px;
		}
		.deck-nav {
			padding-inline: 16px;
		}
		h1 {
			font-size: 30px;
		}
		.overline {
			font-size: 9px;
		}
		.mission-panel {
			padding: 18px;
		}
		.crew-column {
			display: block;
		}
		.goal-panel {
			margin-top: 24px;
		}
		.decision {
			gap: 12px;
			flex-wrap: wrap;
		}
		.dependency {
			margin-left: 36px;
			flex-basis: 100%;
		}
		.world-caption {
			flex-wrap: wrap;
		}
		.resource-strip {
			gap: 10px;
		}
		.fuel-cell {
			gap: 8px;
		}
		.deck-footer {
			flex-direction: column;
			gap: 12px;
		}
		.panel-top {
			align-items: flex-start;
		}
		.decision-panel h2 {
			font-size: 20px;
		}
	}
</style>
