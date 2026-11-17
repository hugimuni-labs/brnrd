<script lang="ts">
	// /account/views: the operators' page-view read-out. Linked from nowhere;
	// the backend answers 404 to anyone not in BRNRD_ADMIN_GITHUB_LOGINS
	// (routers/admin.py), so this page shows nothing to them either.
	import { onMount } from 'svelte';
	import { site } from '$lib/site';
	import {
		dayTotals,
		filterByHost,
		hostLabel,
		hostTotals,
		parsePageViews,
		type PageViews
	} from '$lib/pageViews';

	let data = $state<PageViews | null>(null);
	let phase = $state<'loading' | 'ready' | 'absent'>('loading');
	let query = $state('');

	const shown = $derived(data ? filterByHost(data.rows, query) : []);
	const hosts = $derived(data ? hostTotals(data.rows) : []);
	const days = $derived(dayTotals(shown));

	onMount(async () => {
		try {
			const r = await fetch('/v1/admin/page-views', { cache: 'no-store' });
			data = r.ok ? parsePageViews(await r.json()) : null;
		} catch {
			data = null;
		}
		phase = data ? 'ready' : 'absent';
	});
</script>

<svelte:head><title>brnrd</title></svelte:head>

{#if $site?.deployment === 'loopback'}
	<main data-cloud-page>
		<p>This page lives on brnrd.dev</p>
		<a href="https://brnrd.dev/account/views">Open this page on brnrd.dev</a>
	</main>
{:else if phase === 'absent'}
	<main class="mx-auto max-w-3xl p-6 font-mono text-stone-300"><p>Not found.</p></main>
{:else if phase === 'ready' && data}
	<main class="mx-auto max-w-5xl p-5 font-mono text-sm text-stone-300 sm:p-6">
		<h1 class="text-2xl font-semibold text-amber-100">Page views by referrer</h1>
		<p class="mt-2 text-ink-quiet">
			{data.since} to {data.until} (UTC). Views exclude bots; the bots column is shown apart. Counts are
			a floor: a CDN-served page never reaches the counter.
		</p>

		<section class="mt-6" aria-labelledby="hosts">
			<h2 id="hosts" class="text-lg text-amber-100">Referrers, 30 days</h2>
			<div class="mt-2 flex flex-wrap gap-2">
				{#each hosts as h (h.host)}
					<button
						type="button"
						class="rounded border border-stone-700 px-2 py-1 hover:border-amber-200"
						class:border-amber-200={query === hostLabel(h.host)}
						onclick={() => (query = query === hostLabel(h.host) ? '' : hostLabel(h.host))}
						>{hostLabel(h.host)} · {h.views}</button
					>
				{/each}
			</div>
			<label class="mt-4 block">
				Filter by host
				<input
					bind:value={query}
					placeholder="t.co"
					class="ml-2 rounded border border-stone-700 bg-transparent px-2 py-1"
				/>
			</label>
		</section>

		<section class="mt-6" aria-labelledby="days">
			<h2 id="days" class="text-lg text-amber-100">Per day{query ? ` from ${query}` : ''}</h2>
			<table class="mt-2">
				<tbody>
					{#each days as d (d.day)}
						<tr><td class="pr-6">{d.day}</td><td class="text-right">{d.views}</td></tr>
					{/each}
				</tbody>
			</table>
		</section>

		<section class="mt-6" aria-labelledby="rows">
			<h2 id="rows" class="text-lg text-amber-100">Day · path · referrer</h2>
			<table class="mt-2 w-full">
				<thead class="text-left text-ink-quiet">
					<tr
						><th>day</th><th>path</th><th>referrer</th><th class="text-right">views</th><th
							class="text-right">bots</th
						></tr
					>
				</thead>
				<tbody>
					{#each shown as r (r.day + r.path + r.referer_host)}
						<tr>
							<td class="pr-4">{r.day}</td>
							<td class="pr-4">{r.path}</td>
							<td class="pr-4">{hostLabel(r.referer_host)}</td>
							<td class="text-right">{r.views}</td>
							<td class="text-right text-ink-mute">{r.bot_views}</td>
						</tr>
					{:else}
						<tr><td colspan="5" class="text-ink-mute">No views in the window.</td></tr>
					{/each}
				</tbody>
			</table>
		</section>
	</main>
{/if}
