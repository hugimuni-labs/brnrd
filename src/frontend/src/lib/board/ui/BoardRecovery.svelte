<script lang="ts">
	import { onMount } from 'svelte';
	import { forget } from '../cache.ts';
	import { PLACE, readPlace } from '../place.ts';
	let { message }: { message: string } = $props();

	// The failed boundary has unmounted Board (including its place saver) before this runs.
	// Retry only once per tab: bad data unrelated to the saved place must not make a reload loop.
	onMount(() => {
		void recover();
	});
	async function recover() {
		try {
			if (sessionStorage.getItem('board.recovered')) return;
			const p = readPlace(localStorage.getItem(PLACE));
			sessionStorage.setItem('board.recovered', '1');
			localStorage.setItem(PLACE, JSON.stringify({ ...p, open: [], bead: null, hist: [] }));
			await forget();
			location.reload();
		} catch {
			// Storage may be unavailable. Keep the visible fallback instead of another render error.
		}
	}
</script>

<div role="alert" data-board-error>
	<p>The board could not be drawn. Please reload to try again.</p>
	<code>{message}</code>
	<button type="button" onclick={() => location.reload()}>Reload</button>
</div>
