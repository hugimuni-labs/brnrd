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
		// #885: the bot's own login, to copy, and the repo to link the GitHub
		// collaborators settings page for. Both only render alongside
		// `markerNotice` — a repo in an unknown or already-collaborator state
		// gets neither. May arrive as an empty string rather than omitted, so
		// each is guarded on its own truthiness too.
		botLogin?: string;
		repoFullName?: string;
	}

	let { markerNotice, failureNotice, botLogin = '', repoFullName = '' }: Props = $props();

	let copied = $state(false);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	async function copyBotLogin() {
		if (!botLogin) return;
		try {
			await navigator.clipboard.writeText(botLogin);
			copied = true;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = false), 1500);
		} catch {
			// Clipboard permission denied or unavailable in this context —
			// no crash, just no "copied" flash.
		}
	}
</script>

{#if markerNotice}
	<p class="mt-2 font-mono text-[11px] text-amber-400">
		<span class="text-ink-mute uppercase tracking-wide">marker</span>
		— {markerNotice}
	</p>
	<div class="mt-1 flex flex-wrap items-center gap-3 font-mono text-[11px] text-ink-quiet">
		{#if botLogin}
			<button
				type="button"
				class="cursor-pointer underline hover:text-stone-300"
				onclick={copyBotLogin}>{copied ? 'copied' : `copy @${botLogin}`}</button
			>
		{/if}
		{#if repoFullName}
			<a
				class="underline hover:text-stone-300"
				href={`https://github.com/${repoFullName}/settings/access`}
				rel="external noreferrer"
				target="_blank">open collaborators page</a
			>
		{/if}
	</div>
{/if}
{#if failureNotice}
	<p class="mt-1 font-mono text-[11px] text-amber-700">
		{failureNotice}
	</p>
{/if}
