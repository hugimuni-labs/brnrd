<script lang="ts">
	interface Props {
		// Pre-rendered one-sentence absence line (#874 ask 3), server-owned
		// wording (`github_marker.marker_absence_text`) so it says the same
		// thing everywhere the repo view renders it. Present only when the
		// server positively knows the marker isn't a collaborator — `null`
		// covers both "it is one" and "we don't know", which render nothing
		// here on purpose: a state we can't prove must never read as a claim.
		markerNotice: string | null;
		// Last acceptance/check *failure* (#874 ask 2) — distinct from the
		// absence line above, and never silence: a repo can be a collaborator
		// today and still carry a stale failure from an earlier check.
		failureNotice: string | null;
	}

	let { markerNotice, failureNotice }: Props = $props();
</script>

{#if markerNotice}
	<p class="mt-2 font-mono text-[11px] text-amber-400">
		<span class="text-ink-mute uppercase tracking-wide">marker</span>
		— {markerNotice}
	</p>
{/if}
{#if failureNotice}
	<p class="mt-1 font-mono text-[11px] text-amber-700">
		{failureNotice}
	</p>
{/if}
