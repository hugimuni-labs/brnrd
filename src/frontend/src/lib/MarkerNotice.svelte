<script lang="ts">
	interface Props {
		// The catch site owns classification; this component owns copy. Never
		// accept an exception sentence as input (#969 / #786 precedent).
		status: 'permission-missing' | 'not-a-collaborator' | 'check-unavailable' | 'unknown' | null;
		// #885: the bot's own login, to copy, and the repo to link the GitHub
		// collaborators settings page for. Both only render alongside the
		// `not-a-collaborator` state — an unknown or already-collaborator state
		// gets neither. May arrive as an empty string rather than omitted, so
		// each is guarded on its own truthiness too.
		botLogin?: string;
		repoFullName?: string;
	}

	let { status, botLogin = '', repoFullName = '' }: Props = $props();

	let notice = $derived.by(() => {
		const login = botLogin || 'the configured GitHub bot';
		switch (status) {
			case 'not-a-collaborator':
				return `${login} not a collaborator — assigns / review-requests / comment-tags addressed to it won't reach the resident; invite it in Settings → Collaborators.`;
			case 'permission-missing':
				return "permission missing — the GitHub App lacks the grant for the collaborators endpoint; grant Administration: read in the App's repository permissions.";
			case 'check-unavailable':
				return 'collaborator check unavailable — GitHub could not be reached; try again later.';
			case 'unknown':
				return 'collaborator status unknown — the check failed for an unclassified reason.';
			default:
				return null;
		}
	});

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

{#if notice}
	<p class="mt-2 font-mono text-[11px] text-amber-400">
		<span class="text-ink-mute uppercase tracking-wide">marker</span>
		— {notice}
	</p>
	{#if status === 'not-a-collaborator'}
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
{/if}
