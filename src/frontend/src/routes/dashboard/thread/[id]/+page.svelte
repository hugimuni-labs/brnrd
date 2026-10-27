<script lang="ts">
	import { page } from '$app/state';
	import LoomThreadPage from '$lib/LoomThreadPage.svelte';
</script>

<svelte:head>
	<title>{page.params.id} · brnrd</title>
	<meta name="robots" content="noindex" />
</svelte:head>

{#key page.params.id}
	<LoomThreadPage id={page.params.id ?? ''} />
{/key}
