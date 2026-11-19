<script lang="ts">
	import { onMount } from 'svelte';
	import Dashboard from './Dashboard.svelte';
	import LoomBoard from './LoomBoard.svelte';
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

	type View = 'unknown' | 'board' | 'dashboard';

	let view = $state<View>('unknown');
	let snapshot = $state<LoomSnapshot | null>(null);
	let seenAt = $state<string | null>(null);
	let body = $state<string | null>(null);
	let now = $state(Date.now());

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
			if (read === 'anon' || read === 'none') view = 'dashboard';
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
{:else}
	<Dashboard />
{/if}
