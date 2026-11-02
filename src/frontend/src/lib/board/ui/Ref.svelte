<script lang="ts">
	// A result's or call's ref: a forge link when it names one (forgeHref), plain text otherwise. Its new tab is also
	// the layout's rule for every outbound link (outbound.ts).
	import { forgeHref } from '../bead.ts';
	let { ref }: { ref?: string | null } = $props();
	const href = $derived(forgeHref(ref));
</script>

<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an external link -->
{#if href}<a {href} target="_blank" rel="noopener">{ref}</a>{:else}{ref || ''}{/if}
