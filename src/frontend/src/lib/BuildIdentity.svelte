<script lang="ts">
	// The dashboard's answer to "is my merge live?" (#1734): the deployed
	// build's short commit (linked to the forge) and its age, both
	// best-effort — an absent field simply doesn't render its half of the
	// line rather than guessing. No drift clause here on purpose: see
	// `buildIdentity.ts`'s doc comment and the task report for why the
	// server side of this line stops at its own commit.
	import type { BuildIdentityView } from './buildIdentity';

	let { view }: { view: BuildIdentityView | null } = $props();
</script>

{#if view}
	<p class="mt-0.5 font-mono text-[11px] tracking-wide text-ink-quiet">
		{#if view.commitUrl && view.commitShort}
			<a href={view.commitUrl} rel="external" class="hover:text-stone-300">{view.commitShort}</a>
		{/if}
		{#if view.commitUrl && view.builtAgo}
			<span aria-hidden="true"> · </span>
		{/if}
		{#if view.builtAgo}
			<span>built {view.builtAgo}</span>
		{/if}
	</p>
{/if}
