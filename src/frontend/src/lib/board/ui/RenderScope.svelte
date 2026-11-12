<script lang="ts">
	import type { Snippet } from 'svelte';
	let { name, children }: { name: string; children: Snippet } = $props();
</script>

<!-- Names survive production minification, where Svelte omits its dev component stack.
     Rethrow to board recovery; these scopes never hide a failed subtree. -->
<svelte:boundary
	onerror={(cause) => {
		throw new Error(`${name}: ${cause instanceof Error ? cause.message : String(cause)}`, {
			cause
		});
	}}
>
	{@render children()}
</svelte:boundary>
