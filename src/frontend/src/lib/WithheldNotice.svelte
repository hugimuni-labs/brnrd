<script lang="ts">
	import { resolve } from '$app/paths';
	import { optedOutClause, unrecordedClause } from './publishScope';
	import { consentGapRepos } from './consentGap';
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
	// The in-place act needs a real repo id to call publish-layers with —
	// `consentGapRepos` drops any name that arrived without one (an older
	// backend, mid-deploy). Empty ⇒ no dead button; the /repos link below
	// still covers it.
	let canNameRepo = $derived(consentGapRepos(withheld).length > 0);
</script>

<p class={className}>
	{#if unrecorded === null && optedOut === null}
		<!-- Neither list survived the server's proof — a repo can permit some
		     lanes and not this one, so an unattributed withheld lane is a real
		     state, not a dead branch. -->
		paused — no publish scope
	{:else}
		{#if unrecorded !== null}
			<!-- The clause still names repos only, but WithheldLane now also
			     carries `unrecorded_ids`/`opted_out_ids` in parallel — enough
			     for the in-place act below; the /repos deep link by id stays
			     PublishConsentNotice's own trick (it starts from ConnectedRepo,
			     which always has an id). A plain /repos link still gets the
			     owner to the page that covers everything this dialog doesn't. -->
			paused — {unrecorded}. One scope on the
			<a class="underline hover:text-amber-100" href={resolve('/repos')}>repos page</a> reopens this.
		{/if}
		{#if optedOut !== null}
			{unrecorded !== null ? '' : 'paused — '}{optedOut}.
		{/if}
		{#if canNameRepo}
			<a class="underline hover:text-amber-100" href={resolve('/repos')}>Set the full publish scope.</a>
		{/if}
	{/if}
</p>
