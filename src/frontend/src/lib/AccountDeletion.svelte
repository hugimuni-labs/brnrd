<script lang="ts">
	import { AccountActionError, deleteAccount } from './account';

	// Art 17 erasure, tucked at the foot of §4 "account" rather than beside
	// the subscription controls — findable, not a stray click away from the
	// billing CTAs above it. `githubLogin` is threaded down from the page's
	// own `/v1/dashboard/repos` fetch (repos.ts's `RepoAccount`) rather than
	// re-fetched here: that response already carries it, so a second round
	// trip just to render a confirmation label would be waste.
	let { githubLogin }: { githubLogin: string | null } = $props();

	let open = $state(false);
	let typed = $state('');
	let busy = $state(false);
	let error = $state<string | null>(null);
	let done = $state(false);

	let matches = $derived(githubLogin !== null && typed === githubLogin);

	async function confirmDelete() {
		if (!matches || busy) return;
		busy = true;
		error = null;
		try {
			await deleteAccount(typed);
			done = true;
			// The server has already revoked the session token that issued
			// this request; /logout clears the now-dead cookie client-side
			// and returns to /login, same as any other sign-out.
			window.location.href = '/logout';
		} catch (e) {
			error = e instanceof AccountActionError ? e.message : 'account deletion failed';
			busy = false;
		}
	}
</script>

<div class="mt-6 border border-stone-800 p-4">
	<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">danger zone</p>
	{#if !open}
		<button
			type="button"
			class="mt-2 cursor-pointer border border-stone-700 px-3 py-1.5 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:border-red-800 hover:text-red-400"
			onclick={() => (open = true)}
		>
			delete account…
		</button>
	{:else}
		<p class="mt-2 text-sm leading-relaxed text-stone-400">
			Deletes your repos, daemon connections, sessions and API keys, run and activity history,
			dashboard mirror, and Telegram/GitHub pairings — and cancels any live subscription
			immediately. This cannot be undone.
		</p>
		<p class="mt-2 text-sm leading-relaxed text-stone-400">
			<strong class="text-amber-100">Retained:</strong> your append-only billing ledger (invoices, subscription
			and refund events) is kept for accounting purposes; its statutory retention period is to be confirmed
			with counsel. Stripe separately retains its own copy of your customer and payment records under
			Stripe's own retention policy.
		</p>
		<label
			class="mt-3 block font-mono text-[11px] tracking-wide text-ink-quiet uppercase"
			for="confirm-login"
		>
			type your GitHub login ({githubLogin ?? '…'}) to confirm
		</label>
		<input
			id="confirm-login"
			type="text"
			class="mt-1 w-full max-w-xs border border-stone-700 bg-transparent px-2 py-1.5 font-mono text-sm text-stone-200 focus:border-red-800 focus:outline-none"
			bind:value={typed}
			disabled={busy || githubLogin === null}
			autocomplete="off"
			spellcheck="false"
		/>
		<div class="mt-3 flex flex-wrap items-center gap-3">
			<button
				type="button"
				class="cursor-pointer border border-red-800 bg-red-950/30 px-3 py-1.5 font-mono text-[11px] tracking-wide text-red-400 uppercase hover:bg-red-950/60 disabled:cursor-default disabled:opacity-50"
				disabled={!matches || busy}
				onclick={confirmDelete}
			>
				{busy ? 'deleting…' : done ? 'deleted' : 'permanently delete account'}
			</button>
			<button
				type="button"
				class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				disabled={busy}
				onclick={() => {
					open = false;
					typed = '';
					error = null;
				}}
			>
				cancel
			</button>
		</div>
		{#if error}
			<p class="mt-3 text-sm text-red-400">{error}</p>
		{/if}
	{/if}
</div>
