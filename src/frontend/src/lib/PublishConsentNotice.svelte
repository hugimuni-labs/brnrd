<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ConnectedRepo } from './repos';
	import { optedOutClause, parsePublishLayers, unrecordedClause } from './publishScope';

	interface Props {
		repos: ConnectedRepo[] | null;
	}

	let { repos }: Props = $props();
	let unrecordedRepos = $derived(repos?.filter((repo) => repo.publish_layers == null) ?? []);
	let optedOutRepos = $derived(
		repos?.filter(
			(repo) => repo.publish_layers != null && parsePublishLayers(repo.publish_layers).size === 0
		) ?? []
	);
	// The fact itself lives in publishScope.ts, shared with the lane-local
	// WithheldNotice — this banner only frames it differently (account-level:
	// "something in this account is paused", with the `paused —`/`off —`
	// fragments below instead of WithheldNotice's full sentences). `null` here
	// means "no gap of that kind", not "gap of zero repos".
	let unrecorded = $derived(unrecordedClause(unrecordedRepos.map((repo) => repo.repo_full_name)));
	let optedOut = $derived(optedOutClause(optedOutRepos.map((repo) => repo.repo_full_name)));
	let target = $derived(unrecordedRepos[0] ?? optedOutRepos[0] ?? null);
</script>

{#if unrecorded !== null || optedOut !== null}
	<div class="panel mt-4 border-amber-900/60 p-3 text-sm text-amber-200">
		{#if unrecorded !== null}
			<!-- `publish_layers === null` proves only that no scope was ever
			     recorded — not that this repo predates the consent setting, which
			     a repo minted through the account API today would also satisfy.
			     Say what the data proves and nothing more. -->
			paused — {unrecorded}.
		{/if}
		{#if optedOut !== null}
			{#if unrecorded !== null}
				·
			{/if}
			off — {optedOut}.
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
