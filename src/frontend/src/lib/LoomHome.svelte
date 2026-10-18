<script lang="ts">
	// `/`: the board when the loom has published a snapshot, the dashboard
	// otherwise. Signed out (401) is the dashboard too, which renders its own
	// landing. No snapshot splits once more on the repos fetch: an account no
	// daemon has ever paired with (ColdStart's own `daemonEverPaired` and
	// `machines.paired`) gets the three-noun empty state; anyone already set
	// up the old way keeps the dashboard they know. Any failure falls back to
	// the dashboard, so this route is never worse than it was.
	import { onMount } from 'svelte';
	import Dashboard from './Dashboard.svelte';
	import LoomBoard from './LoomBoard.svelte';
	import LoomEmpty from './LoomEmpty.svelte';
	import { fetchRepos } from './repos.ts';
	import { startVisiblePoll } from './visiblePoll.ts';
	import {
		BODY_KEY,
		SEEN_KEY,
		fetchLoom,
		keptBody,
		newestAt,
		store,
		stored,
		type LoomSnapshot
	} from './loomSnapshot.ts';

	type View = 'unknown' | 'board' | 'empty' | 'dashboard';

	let view = $state<View>('unknown');
	let snapshot = $state<LoomSnapshot | null>(null);
	let seenAt = $state<string | null>(null);
	let body = $state<string | null>(null);
	let now = $state(Date.now());

	async function neverPaired(): Promise<boolean> {
		try {
			const repos = await fetchRepos();
			const daemonEverPaired = (repos.connected_repos ?? []).some((r) =>
				['online', 'offline', 'never_started'].includes(r.daemon_status)
			);
			return !daemonEverPaired && repos.machines?.paired !== true;
		} catch {
			return false;
		}
	}

	function show(next: LoomSnapshot) {
		snapshot = next;
		body ??= keptBody(next);
		view = 'board';
		store(SEEN_KEY, newestAt(next));
	}

	onMount(() => {
		seenAt = stored(SEEN_KEY);
		const clock = setInterval(() => (now = Date.now()), 30_000);
		let stopPoll: (() => void) | null = null;

		(async () => {
			let read;
			try {
				read = await fetchLoom();
			} catch {
				view = 'dashboard';
				return;
			}
			if (read === 'anon') view = 'dashboard';
			else if (read === 'none') view = (await neverPaired()) ? 'empty' : 'dashboard';
			else {
				show(read);
				// The first read already happened; the poll keeps the board current.
				let first = true;
				stopPoll = startVisiblePoll(
					async (signal) => {
						if (first) {
							first = false;
							return;
						}
						const next = await fetchLoom(fetch, signal).catch(() => null);
						if (!signal.aborted && next && typeof next === 'object') show(next);
					},
					document,
					30_000
				);
			}
		})();

		return () => {
			clearInterval(clock);
			stopPoll?.();
		};
	});

	// Only a picked body is kept; with none picked nothing is written, so a
	// stale pick dropped by pickedBody() stays dropped on the next read.
	$effect(() => {
		if (view === 'board' && body) store(BODY_KEY, body);
	});
</script>

{#if view === 'unknown'}
	<main class="flex min-h-[60vh] items-center justify-center">
		<p class="eyebrow">reaching brnrd&hellip;</p>
	</main>
{:else if view === 'board' && snapshot}
	<LoomBoard {snapshot} {seenAt} {now} bind:body />
{:else if view === 'empty'}
	<LoomEmpty />
{:else}
	<Dashboard />
{/if}
