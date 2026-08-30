<script lang="ts">
	// /ascii — the reference camera over the unbounded room (#1652), now a
	// thin route over the extracted field (src/lib/AsciiField.svelte,
	// 2026-08-31): this page owns the URL, the head, the body void, and the
	// ?demo switch; the field owns everything else. /daily embeds the same
	// component as its scene — one renderer, two routes.
	import { onMount } from 'svelte';
	import AsciiField from '$lib/AsciiField.svelte';

	let demo = $state(false);
	let ready = $state(false);
	onMount(() => {
		demo = new URLSearchParams(location.search).has('demo');
		ready = true;
	});
</script>

<svelte:head>
	<title>brnrd · the room, in characters</title>
	<meta name="robots" content="noindex" />
</svelte:head>

{#if ready}
	<AsciiField {demo} />
{/if}

<style>
	:global(body) {
		background: #0c0906;
	}
</style>
