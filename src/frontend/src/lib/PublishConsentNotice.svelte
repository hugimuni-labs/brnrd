<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ConnectedRepo } from './repos';
	import { parsePublishLayers } from './publishScope';

	interface Props {
		repos: ConnectedRepo[] | null;
	}

	let { repos }: Props = $props();
	let unrecorded = $derived(repos?.filter((repo) => repo.publish_layers == null) ?? []);
	let optedOut = $derived(
		repos?.filter(
			(repo) => repo.publish_layers != null && parsePublishLayers(repo.publish_layers).size === 0
		) ?? []
	);
	let target = $derived(unrecorded[0] ?? optedOut[0] ?? null);
</script>

{#if unrecorded.length > 0 || optedOut.length > 0}
	<div class="panel mt-4 border-amber-900/60 p-3 text-sm text-amber-200">
		{#if unrecorded.length > 0}
			paused — these repos were connected before the publish consent existed and have never been
			asked:
			{unrecorded.map((repo) => repo.repo_full_name).join(', ')}
		{/if}
		{#if optedOut.length > 0}
			{#if unrecorded.length > 0}
				·
			{/if}
			off — publish scope is set to nothing:
			{optedOut.map((repo) => repo.repo_full_name).join(', ')}
		{/if}
		·
		<a
			class="underline hover:text-amber-100"
			href={target === null
				? resolve('/repos')
				: resolve(
						`/repos?scope=${encodeURIComponent(target.id)}#repo-${encodeURIComponent(target.id)}`
					)}>set a scope</a
		>
	</div>
{/if}
