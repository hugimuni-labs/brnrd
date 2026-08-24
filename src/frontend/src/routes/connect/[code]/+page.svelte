<script lang="ts">
	import { page } from '$app/state';
	import ConnectFlow from '$lib/ConnectFlow.svelte';

	// Deep-link entry: the full /connect/<code>#<code> URL a user copies from
	// the terminal. ConnectFlow receives the code and fragment and starts at
	// the confirm phase directly, fetching context on mount.
	let code = $derived(page.params.code ?? '');
	let hash = $derived(page.url.hash ?? '');
</script>

<ConnectFlow initialCode={code} initialHash={hash} />
