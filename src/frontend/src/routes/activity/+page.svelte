<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import {
		ActivityAuthError,
		fetchActivity,
		type ActivityResponse,
		type ActivityRow
	} from '$lib/activity';
	import { ageSince } from '$lib/liveRuns';
	import {
		STATUS_CRITICAL,
		STATUS_GOOD,
		STATUS_UNKNOWN,
		STATUS_WARN,
		statusDotStyle
	} from '$lib/statusPalette';

	// #327 Jinja-removal, /activity half: the full activity history the
	// legacy page rendered unbounded (282 records, live 2026-07-10), now
	// bounded server-side with a client "show more". Filters are pill
	// toggles, not <select> dropdowns — same zero-dropdown grammar the #328
	// design settled for the dashboard's other surfaces.
	//
	// 5s poll, not the dashboard's 2s: this is a history feed, not a
	// watch-it-tick surface — the daemon publishes activity on its own
	// ~25-30s loop anyway.
	const POLL_MS = 5_000;
	const TICK_MS = 1_000;
	const PAGE_SIZE = 100;
	const LIMIT_CAP = 300; // server-enforced too (dashboard_activity_api)

	let data = $state<ActivityResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let now = $state(Date.now());

	let repoId = $state('');
	let kind = $state('');
	let status = $state('');
	let limit = $state(PAGE_SIZE);

	let pollHandle: ReturnType<typeof setInterval> | undefined;
	let tickHandle: ReturnType<typeof setInterval> | undefined;

	async function refresh() {
		try {
			data = await fetchActivity({
				repo_id: repoId || undefined,
				kind: kind || undefined,
				status: status || undefined,
				limit
			});
			error = null;
			unauthenticated = false;
		} catch (e) {
			if (e instanceof ActivityAuthError) {
				unauthenticated = true;
			} else {
				error = e instanceof Error ? e.message : 'activity fetch failed';
			}
		}
	}

	function setFilter(which: 'repo' | 'kind' | 'status', value: string) {
		if (which === 'repo') repoId = repoId === value ? '' : value;
		if (which === 'kind') kind = kind === value ? '' : value;
		if (which === 'status') status = status === value ? '' : value;
		limit = PAGE_SIZE;
		refresh();
	}

	function showMore() {
		limit = Math.min(limit + PAGE_SIZE, LIMIT_CAP);
		refresh();
	}

	// Same register as statusPalette's own semantics: amber = alive
	// (running), frost = waiting/cooling (pending, scheduled, parked),
	// void ash = spent badly (failed), stone = receded (completed, unknown).
	// Never color alone — the bucket text always renders beside the dot.
	function bucketColor(bucket: string): string {
		if (bucket === 'running') return STATUS_GOOD;
		if (bucket === 'pending' || bucket === 'scheduled' || bucket === 'parked') return STATUS_WARN;
		if (bucket === 'failed') return STATUS_CRITICAL;
		return STATUS_UNKNOWN;
	}

	function clock(iso: string | null): string {
		if (!iso) return '—';
		const t = Date.parse(iso);
		return Number.isNaN(t) ? '—' : new Date(t).toLocaleString();
	}

	// Elapsed mirrors the legacy `_duration_label` boundary: a still-live
	// bucket measures against now; a settled one against its last update.
	function elapsed(row: ActivityRow): string | null {
		if (!row.started_at) return null;
		const start = Date.parse(row.started_at);
		if (Number.isNaN(start)) return null;
		const live = row.bucket === 'running' || row.bucket === 'pending';
		const endIso = row.updated_at ?? row.reported_at;
		const end = live ? now : endIso ? Date.parse(endIso) : now;
		const s = Math.max(0, Math.floor(((Number.isNaN(end) ? now : end) - start) / 1000));
		if (s < 90) return `${s}s`;
		const m = Math.floor(s / 60);
		if (m < 90) return `${m}m`;
		const h = Math.floor(m / 60);
		if (h < 48) return `${h}h ${String(m % 60).padStart(2, '0')}m`;
		return `${Math.floor(h / 24)}d ${String(h % 24).padStart(2, '0')}h`;
	}

	function linkEntries(links: Record<string, string>): [string, string][] {
		return Object.entries(links).filter(([, href]) => Boolean(href));
	}

	onMount(() => {
		refresh();
		pollHandle = setInterval(refresh, POLL_MS);
		tickHandle = setInterval(() => {
			now = Date.now();
		}, TICK_MS);
	});

	onDestroy(() => {
		if (pollHandle) clearInterval(pollHandle);
		if (tickHandle) clearInterval(tickHandle);
	});
</script>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · activity</p>
		<a
			href="/"
			class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
			>← dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">activity</h1>
	<p class="mt-2 text-sm text-stone-400">
		Everything connected daemons have reported — runs, scheduled wakes, parked respawns.
	</p>

	{#if unauthenticated}
		<p class="mt-6 text-sm text-stone-400">
			Sign in to see activity — <a
				class="text-sky-400 underline"
				href="/login?next=/activity"
				rel="external">log in</a
			>.
		</p>
	{:else if error}
		<p class="mt-6 text-sm text-red-400">{error}</p>
	{:else if data === null}
		<p class="mt-6 text-sm text-stone-500">Loading…</p>
	{:else}
		{#if data.repos.length > 1 || data.kinds.length > 0 || data.statuses.length > 0}
			<div class="mt-5 space-y-2">
				{#if data.repos.length > 1}
					<div class="flex flex-wrap items-baseline gap-1.5">
						<span class="w-12 font-mono text-[10px] tracking-wide text-stone-600 uppercase"
							>repo</span
						>
						{#each data.repos as repo (repo.id)}
							<button
								type="button"
								class={`cursor-pointer border px-1.5 py-0.5 font-mono text-[10px] tracking-wide uppercase ${repoId === repo.id ? 'border-amber-700 bg-amber-950/40 text-amber-200' : 'border-stone-800 text-stone-500 hover:text-stone-300'}`}
								onclick={() => setFilter('repo', repo.id)}>{repo.label}</button
							>
						{/each}
					</div>
				{/if}
				<div class="flex flex-wrap items-baseline gap-1.5">
					<span class="w-12 font-mono text-[10px] tracking-wide text-stone-600 uppercase">kind</span
					>
					{#each data.kinds as item (item)}
						<button
							type="button"
							class={`cursor-pointer border px-1.5 py-0.5 font-mono text-[10px] tracking-wide uppercase ${kind === item ? 'border-amber-700 bg-amber-950/40 text-amber-200' : 'border-stone-800 text-stone-500 hover:text-stone-300'}`}
							onclick={() => setFilter('kind', item)}>{item}</button
						>
					{/each}
				</div>
				<div class="flex flex-wrap items-baseline gap-1.5">
					<span class="w-12 font-mono text-[10px] tracking-wide text-stone-600 uppercase"
						>status</span
					>
					{#each data.statuses as item (item)}
						<button
							type="button"
							class={`cursor-pointer border px-1.5 py-0.5 font-mono text-[10px] tracking-wide uppercase ${status === item ? 'border-amber-700 bg-amber-950/40 text-amber-200' : 'border-stone-800 text-stone-500 hover:text-stone-300'}`}
							onclick={() => setFilter('status', item)}>{item}</button
						>
					{/each}
				</div>
			</div>
		{/if}

		<div class="mt-4">
			{#if data.rows.length === 0}
				<p class="text-sm text-stone-500">No matching activity.</p>
			{:else}
				<div class="space-y-2">
					{#each data.rows as row (row.repo_label + row.id)}
						{@const color = bucketColor(row.bucket)}
						{@const level = row.bucket === 'failed' ? 'critical' : row.bucket}
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
										style={statusDotStyle(level, color)}
										aria-hidden="true"
									></span>
									<span
										class="shrink-0 font-mono font-medium tracking-wide uppercase"
										style={`color: ${color}`}>{row.status || row.bucket}</span
									>
									<span
										class="shrink-0 border border-stone-800 px-1 py-0.5 font-mono text-[9px] tracking-wide text-stone-500 uppercase"
										>{row.kind}</span
									>
									{#if row.phase && row.phase !== row.status}
										<span class="truncate font-mono text-[10px] text-stone-500">{row.phase}</span>
									{/if}
								</span>
								<span class="shrink-0 font-mono text-[10px] text-stone-500">
									{ageSince(row.updated_at ?? row.reported_at, now) ?? ''}
								</span>
							</div>
							<p class="mt-1.5 truncate font-medium text-amber-100" title={row.summary}>
								{row.summary}
							</p>
							<p class="truncate text-stone-500">
								{row.repo_label} · {row.source}{row.daemon_name
									? ` · ${row.daemon_name}`
									: ''}{row.conversation_key ? ` · ${row.conversation_key}` : ''}
							</p>
							<div
								class="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 font-mono text-[10px] text-stone-600"
							>
								{#if row.runner.summary}<span>{row.runner.summary}</span>{/if}
								{#if row.branch}<span>{row.branch}</span>{/if}
								{#if row.pr_number}<span>PR #{row.pr_number}</span>{/if}
								{#if elapsed(row)}<span>elapsed {elapsed(row)}</span>{/if}
								{#if row.scheduled_for}<span>scheduled {clock(row.scheduled_for)}</span>{/if}
								{#if row.defer_until}<span>deferred to {clock(row.defer_until)}</span>{/if}
								{#each linkEntries(row.links) as [label, href] (label)}
									<a class="text-sky-400 underline" {href}>{label}</a>
								{/each}
							</div>
						</div>
					{/each}
				</div>
				<div class="mt-3 flex items-center justify-between">
					<p class="font-mono text-[11px] text-stone-600">
						showing {data.rows.length} of {data.total} records
					</p>
					{#if data.rows.length < data.total && limit < LIMIT_CAP}
						<button
							type="button"
							class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200"
							onclick={showMore}>show more</button
						>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>
