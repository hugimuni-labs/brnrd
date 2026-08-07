<script lang="ts">
	interface Props {
		// The catch site owns classification; this component owns copy. Never
		// accept an exception sentence as input (#969 / #786 precedent).
		status:
			| 'permission-missing'
			| 'not-a-collaborator'
			| 'check-unavailable'
			| 'not-configured'
			| 'unknown'
			| null;
		// #885: the bot's own login, to copy, and the repo to link the GitHub
		// collaborators settings page for. Both only render alongside the
		// `not-a-collaborator` state — an unknown or already-collaborator state
		// gets neither. May arrive as an empty string rather than omitted, so
		// each is guarded on its own truthiness too.
		botLogin?: string;
		repoFullName?: string;
		// #1141 — whether the marker check actually succeeded and found the
		// bot a collaborator. `status` alone can't drive the lit line: it's
		// `null` for both "confirmed collaborator" and "never checked", and
		// those must not render identically (the house's dominant failure
		// class — a surface that narrows renders as if it hadn't).
		collaborator?: boolean | null;
		// Pre-rendered age of the last check ("never" when none has run).
		checkedLabel?: string;
	}

	let {
		status,
		botLogin = '',
		repoFullName = '',
		collaborator = null,
		checkedLabel = ''
	}: Props = $props();

	let notice = $derived.by(() => {
		const login = botLogin || 'the configured GitHub bot';
		switch (status) {
			case 'not-a-collaborator':
				// Re-registered 2026-08-04 (`docs/concepts/gates.md`): the
				// App-native `brnrd` label is already the universal summons —
				// this is not a remediation notice for a broken mention path.
				// The invite is an optional upgrade that only buys GitHub's own
				// assignee/reviewer/@-autocomplete affordances, which an App
				// cannot hold on its own.
				return `${login} isn't a collaborator — optional, not required: the brnrd label already summons it. Invite it in Settings → Collaborators to add assignment, review requests, and @ autocomplete.`;
			case 'permission-missing':
				// Rewritten 2026-08-05 (#1141): the old copy told the reader to
				// grant `Administration: read` in the App's repository
				// permissions — wrong principal (this check never used the
				// App's grant at all when this text was written), wrong
				// permission (the check needs only `Metadata: read`, which the
				// App already holds), and not something any end user can act
				// on regardless — the App's grants are the brnrd operator's to
				// change, not a repo-connecting user's. Name the fact and the
				// owner, not a remedy the reader cannot apply.
				return "collaborator status unavailable — brnrd's own check against GitHub failed; this is on the brnrd operator to fix, not something to change here.";
			case 'check-unavailable':
				return 'collaborator check unavailable — GitHub could not be reached; try again later.';
			case 'not-configured':
				return 'collaborator check not run — github_bot_login is not configured; set it in the server settings.';
			case 'unknown':
				return 'collaborator status unknown — the check failed for an unclassified reason.';
			default:
				return null;
		}
	});

	// Neither state is something the reader must act on right now: an
	// operator-scope check failure isn't the reader's job, and the marker
	// being absent is an optional upgrade, not a fault (both re-registered
	// 2026-08-05, #1141) — so both render at the same quiet weight as the
	// lit line below, instead of borrowing the urgent amber a real
	// actionable notice (`check-unavailable`, `not-configured`) still uses.
	let quiet = $derived(status === 'permission-missing' || status === 'not-a-collaborator');

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
	<p class="mt-2 font-mono text-[11px] {quiet ? 'text-ink-quiet' : 'text-amber-400'}">
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
{:else if collaborator === true}
	<!-- #1141 — the satisfied state. Before this, "invited and accepted" and
	     "never checked" both rendered nothing: byte-identical to a reader,
	     the house's dominant failure class. A quiet, non-amber line, same
	     register as the notices above, never the urgent tone a fault would
	     get. -->
	<p class="mt-2 font-mono text-[11px] text-ink-quiet">
		<span class="text-ink-mute uppercase tracking-wide">marker</span>
		— {botLogin || 'the configured GitHub bot'} is a collaborator, checked {checkedLabel ||
			'recently'}.
	</p>
{/if}
