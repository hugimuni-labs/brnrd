<script lang="ts">
	import { resolve } from '$app/paths';
	import { optedOutClause, unrecordedClause } from './publishScope';
	import type { WithheldLane } from './withheld';

	interface Props {
		withheld: WithheldLane;
		class?: string;
	}

	// Default matches the sentence every panel hand-copied before this
	// component existed; a few call sites carry extra spacing utilities
	// (`mt-2`) their layout needs, hence the override.
	let { withheld, class: className = 'text-sm text-amber-200' }: Props = $props();

	// The fact itself lives in publishScope.ts, shared with the account-level
	// PublishConsentNotice banner — this component only frames it (lane-local:
	// "this panel is empty because of it").
	let unrecorded = $derived(unrecordedClause(withheld.unrecorded ?? []));
	let optedOut = $derived(optedOutClause(withheld.opted_out ?? []));
</script>

<p class={className}>
	{#if unrecorded === null && optedOut === null}
		<!-- Neither list survived the server's proof — a repo can permit some
		     lanes and not this one, so an unattributed withheld lane is a real
		     state, not a dead branch. -->
		paused — no publish scope
	{:else}
		{#if unrecorded !== null}
			<!-- WithheldNotice only receives repo *names* from the server
			     (`withheld.unrecorded`/`opted_out` on the dashboard endpoints),
			     never ids, so unlike PublishConsentNotice it cannot build a
			     `/repos?scope=<id>` deep link to the specific row. A plain
			     /repos link still gets the owner to the page that fixes it. -->
			paused — {unrecorded}. One scope on the
			<a class="underline hover:text-amber-100" href={resolve('/repos')}>repos page</a> reopens this.
		{/if}
		{#if optedOut !== null}
			{unrecorded !== null ? '' : 'paused — '}{optedOut}.
		{/if}
	{/if}
</p>
