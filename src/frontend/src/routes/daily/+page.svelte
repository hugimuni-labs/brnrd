<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import AsciiField from '$lib/AsciiField.svelte';
	import LiveRuns from '$lib/LiveRuns.svelte';
	import RunOverlay from '$lib/RunOverlay.svelte';
	import DailyItemPanel from '$lib/daily/DailyItemPanel.svelte';
	import { fetchLiveRuns, type LiveRun } from '$lib/liveRuns';
	import { fetchRunLedger, relicLabel, type RunLedgerRow } from '$lib/runLedger';
	import { fetchSurface, type SurfaceResponse } from '$lib/surface';
	import { fetchRepos } from '$lib/repos';
	import { buildWarpGraph } from '$lib/warpGraph';
	import {
		dailyBuoys,
		dailyIslands,
		dailyLiveBars,
		hashItemId,
		knowledgePageCount,
		surfaceBuoys
	} from '$lib/daily/daily';

	const POLL_MS = 2_000;
	let runs = $state<LiveRun[]>([]);
	let rows = $state<RunLedgerRow[]>([]);
	let surface = $state<SurfaceResponse | null>(null);
	let account = $state('home');
	let stale = $state(false);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedRun = $state<LiveRun | null>(null);
	let now = $state(Date.now());
	let timer: ReturnType<typeof setInterval> | null = null;

	let graph = $derived(buildWarpGraph(surface?.files ?? []));
	let bars = $derived(dailyLiveBars(runs));
	let buoys = $derived(dailyBuoys(graph));
	// The buoy anchor: `/daily#<item-id>`, read on mount for a cold deep link
	// and kept live via `hashchange` for a manual URL edit or the back
	// button. `selectedItem` resolving to `null` until the surface load
	// lands is deliberate — the panel simply appears once the graph has the
	// id, rather than the page needing to know load order.
	let selectedItemId = $state<string | null>(null);
	let selectedItem = $derived(selectedItemId ? (graph.itemById.get(selectedItemId) ?? null) : null);
	let expandedBuoys = $state(false);
	const BUOY_CAP = 10;
	let overCap = $derived(buoys.length > BUOY_CAP);
	let buoyField = $derived(surfaceBuoys(buoys, expandedBuoys ? Infinity : BUOY_CAP));
	let islands = $derived(dailyIslands(runs, rows));
	let kbPages = $derived(knowledgePageCount(surface?.files ?? []));

	async function load(includeStable = false) {
		const requests: Promise<unknown>[] = [fetchLiveRuns(), fetchRunLedger(fetch, 8)];
		if (includeStable) requests.push(fetchSurface(), fetchRepos());
		const results = await Promise.allSettled(requests);
		const live = results[0];
		const ledger = results[1];
		if (live.status === 'fulfilled') {
			const value = live.value as Awaited<ReturnType<typeof fetchLiveRuns>>;
			runs = value.runs.filter((run) => !run.daemon_stale);
			stale = value.stale;
		} else if (includeStable)
			error = live.reason instanceof Error ? live.reason.message : 'live wire unavailable';
		if (ledger.status === 'fulfilled')
			rows = (ledger.value as Awaited<ReturnType<typeof fetchRunLedger>>).rows;
		if (includeStable && results[2]?.status === 'fulfilled')
			surface = results[2].value as SurfaceResponse;
		if (includeStable && results[3]?.status === 'fulfilled') {
			const repos = results[3].value as Awaited<ReturnType<typeof fetchRepos>>;
			account = repos.account.github_login || 'home';
		}
		loading = false;
		now = Date.now();
	}

	function syncItemFromHash() {
		selectedItemId = hashItemId(window.location.hash);
	}

	/** A buoy press: same address a plain `<a href>` already carries (so a
	 *  modified click — new tab, new window — still works untouched), but a
	 *  plain click opens the item in place instead of leaving the page. */
	function openItem(event: MouseEvent, id: string) {
		if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
			return;
		event.preventDefault();
		history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${id}`);
		selectedItemId = id;
	}

	function closeItem() {
		history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
		selectedItemId = null;
	}

	onMount(() => {
		void load(true);
		timer = setInterval(() => void load(false), POLL_MS);
		syncItemFromHash();
		window.addEventListener('hashchange', syncItemFromHash);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
		if (typeof window !== 'undefined') window.removeEventListener('hashchange', syncItemFromHash);
	});
</script>

<svelte:head><title>daily · brnrd</title></svelte:head>

<main class="daily-shell mx-auto min-h-screen max-w-6xl px-3 py-4 sm:px-6 sm:py-7">
	<header class="mb-3 flex items-end justify-between gap-4">
		<div>
			<p class="eyebrow">the water line</p>
			<h1 class="font-mono text-xl font-semibold tracking-tight text-amber-100">daily</h1>
		</div>
		<a
			class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-amber-200"
			href={resolve('/')}>dashboard ↗</a
		>
	</header>

	<section class="live-rack" aria-labelledby="live-heading">
		<div class="section-heading">
			<h2 id="live-heading">now</h2>
			<span>{runs.length} awake{stale ? ' · stale' : ''}</span>
		</div>
		{#if bars.length === 0}
			<p class="quiet-row">the surface is still — choose a buoy when the next wake is ready.</p>
		{:else}
			<ul class="space-y-1" aria-label="live runs">
				{#each bars as bar (bar.run.id)}
					<li style={`--nest: ${bar.depth}`}>
						<button class="live-bar" type="button" onclick={() => (selectedRun = bar.run)}>
							<span class="run-face"
								>{bar.run.mood_rest || bar.run.mood_glyph || (bar.depth ? 'a' : 'b·_·d')}</span
							>
							<span class="run-name">{bar.name}</span>
							{#if bar.act}<span class="run-act">{bar.act}</span>{/if}
							{#if bar.course}<span class="datum">C {bar.course}</span>{/if}
							{#if bar.pending > 0}<span class="datum">✉ {bar.pending}</span>{/if}
							<span class="open-mark">▸</span>
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<section class="field" aria-label="the room, live">
		<div class="section-heading">
			<h2 id="field-heading">the field</h2>
			<span>drag / arrows · f follow</span>
		</div>
		<div class="field-frame">
			<AsciiField rows={22} header={false} legendDefault={false} />
		</div>
	</section>

	<section class="world" aria-label="the account water line">
		<div class="surface-strip">
			<div class="raft">
				<span aria-hidden="true">⛁</span>
				<div><small>raft · account</small><strong>{account}</strong></div>
			</div>
			<div class="buoy-field" aria-label="ready warp items">
				{#each buoyField.shown as buoy (buoy.item.id)}
					<a
						href={`#${buoy.item.id}`}
						class:call={buoy.item.type !== 'action'}
						class="buoy"
						style={`--thread: ${buoy.color}`}
						title={buoy.topic ?? buoy.item.type ?? 'untyped'}
						onclick={(event) => openItem(event, buoy.item.id)}
					>
						<span aria-hidden="true">{buoy.mark}</span><b>{buoy.item.id}</b><span
							>{buoy.item.headline}</span
						>
					</a>
				{/each}
				{#if buoyField.hidden > 0}
					<button
						type="button"
						class="buoy more-buoys"
						title="show the rest of the ready warp"
						onclick={() => (expandedBuoys = true)}>+{buoyField.hidden} more</button
					>
				{:else if expandedBuoys && overCap}
					<button type="button" class="buoy more-buoys" onclick={() => (expandedBuoys = false)}
						>show fewer</button
					>
				{/if}
				{#if !loading && buoys.length === 0}<span class="empty-buoys">no ready buoys</span>{/if}
			</div>
		</div>

		<div class="above-water">
			<div class="band-label"><span>above water</span><strong>in flight</strong></div>
			{#if islands.length === 0}
				<p class="terrain-gap">no branch or PR terrain is served right now.</p>
			{:else}
				<div class="island-grid">
					{#each islands as island (island.repo)}
						<article class="island">
							<header>
								<span>island</span>
								<h3>{island.repo}</h3>
							</header>
							<div class="trunk">trunk ═════════════</div>
							<ul>
								{#each island.branches as branch (branch.name)}
									<li class:burning={branch.live}>
										<span>{branch.live ? '@ camp' : 'scaffold'}</span><b>{branch.name}</b
										>{#if branch.pr}<em>~&gt; PR #{branch.pr}</em>{/if}
									</li>
								{/each}
							</ul>
						</article>
					{/each}
				</div>
			{/if}
		</div>

		<div class="below-water">
			<div class="band-label"><span>below water</span><strong>settled</strong></div>
			<div class="settled-grid">
				<section class="reef">
					<p>⌂ kb reef</p>
					<span title="the kb count — no in-page browser here yet"
						>{kbPages === null ? 'library' : `${kbPages} pages`}</span
					>
				</section>
				<section class="cloth-rows">
					<h3>recent cloth</h3>
					{#if rows.length === 0}<p>no recent cuts served.</p>{:else}
						<ul>
							{#each rows.slice(0, 6) as row, i (`${row.run_id}-${i}`)}<li>
									<span>≡</span><b>{row.name || row.run_id || 'run'}</b><small
										>{row.repo_label || 'unknown project'}</small
									>{#if row.external_refs?.[0]}<em>{relicLabel(row.external_refs[0])}</em>{/if}
								</li>{/each}
						</ul>
					{/if}
				</section>
			</div>
		</div>
	</section>

	{#if error}<p class="mt-3 font-mono text-xs text-red-400">{error}</p>{/if}
</main>

{#if selectedRun}
	<RunOverlay label={selectedRun.name || selectedRun.run_id} onClose={() => (selectedRun = null)}>
		<LiveRuns runs={[selectedRun]} {stale} {now} />
	</RunOverlay>
{/if}

{#if selectedItem}
	<RunOverlay label={selectedItem.headline} onClose={closeItem}>
		<DailyItemPanel item={selectedItem} {graph} />
	</RunOverlay>
{/if}

<style>
	.daily-shell {
		--amber: #d9a441;
		--deep: #100b06;
		font-family: ui-sans-serif, system-ui, sans-serif;
	}
	.section-heading,
	.band-label {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
		font-family: ui-monospace, monospace;
		text-transform: uppercase;
		letter-spacing: 0.14em;
	}
	.section-heading {
		margin-bottom: 0.45rem;
		color: #d6b878;
		font-size: 0.65rem;
	}
	.section-heading h2 {
		font-size: 0.72rem;
		font-weight: 700;
	}
	.live-rack {
		position: relative;
		z-index: 2;
		margin-bottom: 1rem;
	}
	.live-bar {
		width: calc(100% - var(--nest) * 1.25rem);
		margin-left: calc(var(--nest) * 1.25rem);
		display: grid;
		grid-template-columns: auto minmax(7rem, auto) minmax(8rem, 1fr) auto auto auto;
		align-items: center;
		gap: 0.65rem;
		border: 1px solid rgba(217, 164, 65, 0.27);
		background: rgba(35, 25, 13, 0.72);
		padding: 0.48rem 0.65rem;
		text-align: left;
		font-family: ui-monospace, monospace;
		font-size: 0.68rem;
		color: #d6d3d1;
	}
	.live-bar:hover {
		border-color: rgba(251, 191, 36, 0.68);
		background: rgba(65, 42, 16, 0.72);
	}
	.run-face,
	.datum {
		color: #fbbf24;
		white-space: nowrap;
	}
	.run-name {
		color: #fef3c7;
		font-weight: 700;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.run-act {
		color: #a8a29e;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.open-mark {
		color: #8a827a;
	}
	.quiet-row {
		border: 1px dashed rgba(217, 164, 65, 0.22);
		padding: 0.7rem;
		font-family: ui-monospace, monospace;
		font-size: 0.7rem;
		color: #a8a29e;
	}
	.field {
		margin-bottom: 1rem;
	}
	.field-frame {
		overflow: hidden;
		border: 1px solid rgba(217, 164, 65, 0.3);
		background: #0c0906;
		box-shadow: 0 0 35px rgba(217, 164, 65, 0.05);
	}
	.world {
		overflow: hidden;
		border: 1px solid rgba(217, 164, 65, 0.3);
		background: linear-gradient(#171008 0 18%, #100c08 18% 61%, #090706 61%);
		box-shadow: 0 0 35px rgba(217, 164, 65, 0.05);
	}
	.surface-strip {
		min-height: 8.5rem;
		display: flex;
		align-items: center;
		gap: 1.2rem;
		padding: 1.25rem;
		border-top: 2px solid rgba(251, 191, 36, 0.72);
		border-bottom: 2px solid rgba(251, 191, 36, 0.55);
		background: linear-gradient(
			180deg,
			rgba(74, 47, 16, 0.28),
			rgba(217, 164, 65, 0.08) 49%,
			rgba(38, 27, 17, 0.56) 50%
		);
	}
	.raft {
		flex: 0 0 auto;
		display: flex;
		align-items: center;
		gap: 0.7rem;
		border: 1px solid rgba(251, 191, 36, 0.55);
		background: #171008;
		padding: 0.7rem 0.85rem;
		color: #fbbf24;
		box-shadow: 0 0 18px rgba(217, 164, 65, 0.13);
	}
	.raft > span {
		font-size: 1.6rem;
	}
	.raft small {
		display: block;
		font:
			600 0.52rem ui-monospace,
			monospace;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: #8a827a;
	}
	.raft strong {
		display: block;
		max-width: 16rem;
		overflow: hidden;
		text-overflow: ellipsis;
		font:
			700 0.8rem ui-monospace,
			monospace;
		color: #fef3c7;
	}
	.buoy-field {
		display: flex;
		flex: 1;
		flex-wrap: wrap;
		gap: 0.45rem;
		align-items: center;
	}
	.buoy {
		--thread: #d9a441;
		display: flex;
		max-width: 17rem;
		align-items: center;
		gap: 0.35rem;
		border-top: 2px solid var(--thread);
		background: rgba(12, 9, 6, 0.76);
		padding: 0.4rem 0.55rem;
		font:
			600 0.62rem ui-monospace,
			monospace;
		color: #d6d3d1;
	}
	.buoy > span:last-child {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.buoy b {
		color: var(--thread);
		white-space: nowrap;
	}
	.buoy.call {
		border-style: double;
	}
	.more-buoys {
		border-top-style: dashed;
		color: #d6b878;
	}
	.empty-buoys {
		font:
			italic 0.7rem ui-monospace,
			monospace;
		color: #8a827a;
	}
	.above-water {
		min-height: 15rem;
		padding: 1rem 1.2rem 1.35rem;
		background: linear-gradient(rgba(31, 22, 14, 0.76), rgba(18, 13, 9, 0.92));
	}
	.band-label {
		margin-bottom: 1rem;
		color: #8a827a;
		font-size: 0.56rem;
	}
	.band-label strong {
		color: #d6b878;
	}
	.island-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
		gap: 1rem;
	}
	.island {
		/* The silhouette lives on ::before, NOT on the content box: a
		   clip-path here cut the header's own text at both viewports
		   ("ISLAND" → "SLAND") — terrain may shape the ground, never the
		   words standing on it. `isolation` keeps the z-index:-1 layer
		   inside this island instead of dropping behind .world. */
		position: relative;
		isolation: isolate;
		align-self: end;
		border-bottom: 3px double rgba(217, 164, 65, 0.42);
		padding: 1.2rem 0.8rem 0.65rem;
	}
	.island::before {
		content: '';
		position: absolute;
		inset: 0;
		z-index: -1;
		background: linear-gradient(145deg, rgba(84, 56, 24, 0.27), rgba(25, 18, 11, 0.2));
		clip-path: polygon(4% 12%, 16% 0, 78% 0, 96% 22%, 100% 100%, 0 100%);
	}
	.island header span {
		font:
			500 0.5rem ui-monospace,
			monospace;
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: #8a827a;
	}
	.island h3 {
		font:
			700 0.83rem ui-monospace,
			monospace;
		color: #fef3c7;
	}
	.trunk {
		margin: 0.5rem 0;
		color: #8a6c38;
		font:
			500 0.58rem ui-monospace,
			monospace;
		overflow: hidden;
	}
	.island li {
		display: flex;
		flex-wrap: wrap;
		gap: 0.45rem;
		border-left: 2px solid #6b4c24;
		padding: 0.25rem 0.45rem;
		font:
			0.59rem ui-monospace,
			monospace;
		color: #a8a29e;
	}
	.island li.burning {
		border-color: #fbbf24;
		background: rgba(217, 164, 65, 0.06);
	}
	.island li b {
		color: #d6d3d1;
	}
	.island li em {
		margin-left: auto;
		color: #d9a441;
		font-style: normal;
	}
	.terrain-gap {
		font:
			italic 0.7rem ui-monospace,
			monospace;
		color: #8a827a;
	}
	.below-water {
		padding: 1.1rem 1.2rem 1.4rem;
		border-top: 1px solid rgba(217, 164, 65, 0.2);
		background:
			repeating-linear-gradient(0deg, rgba(217, 164, 65, 0.018) 0 1px, transparent 1px 4px), #090706;
	}
	.settled-grid {
		display: grid;
		grid-template-columns: minmax(8rem, 0.7fr) 2fr;
		gap: 1rem;
	}
	.reef,
	.cloth-rows {
		border: 1px solid rgba(120, 95, 55, 0.25);
		background: rgba(4, 3, 2, 0.48);
		padding: 0.8rem;
	}
	.reef p,
	.cloth-rows h3 {
		font:
			700 0.62rem ui-monospace,
			monospace;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: #a98b58;
	}
	.reef span {
		display: block;
		margin-top: 0.6rem;
		font:
			700 1rem ui-monospace,
			monospace;
		color: #d9a441;
	}
	.cloth-rows li {
		display: grid;
		grid-template-columns: auto minmax(7rem, 1fr) minmax(4rem, auto) minmax(0, 12rem);
		gap: 0.5rem;
		padding: 0.27rem 0;
		border-bottom: 1px solid rgba(120, 95, 55, 0.13);
		font:
			0.58rem ui-monospace,
			monospace;
		color: #8a827a;
	}
	.cloth-rows li b {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: #c7b79f;
	}
	.cloth-rows li em {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-style: normal;
		color: #a98b58;
	}
	.cloth-rows p {
		font:
			italic 0.65rem ui-monospace,
			monospace;
		color: #8a827a;
	}
	@media (max-width: 600px) {
		.daily-shell {
			padding: 0.65rem;
		}
		.live-bar {
			grid-template-columns: auto minmax(0, 1fr) auto auto;
			gap: 0.4rem;
		}
		.run-act {
			grid-column: 2/-1;
			font-size: 0.58rem;
		}
		.open-mark {
			display: none;
		}
		.surface-strip {
			align-items: stretch;
			flex-direction: column;
			padding: 0.8rem;
			min-height: 11rem;
		}
		.raft strong {
			max-width: 14rem;
		}
		.buoy-field {
			display: grid;
			grid-template-columns: 1fr;
		}
		.buoy {
			max-width: none;
		}
		.above-water,
		.below-water {
			padding: 0.8rem;
		}
		.island-grid,
		.settled-grid {
			grid-template-columns: 1fr;
		}
		.cloth-rows li {
			grid-template-columns: auto minmax(0, 1fr) auto;
		}
		.cloth-rows li small {
			display: none;
		}
	}
</style>
