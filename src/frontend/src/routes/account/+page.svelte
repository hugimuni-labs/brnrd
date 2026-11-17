<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import AccountDeletion from '$lib/AccountDeletion.svelte';
	import BillingPanel from '$lib/BillingPanel.svelte';
	import { ACCOUNT_PATH, loginFor, readAccount, type AccountRead } from '$lib/accountPage';

	// The subscription and the account, reachable from every signed-in page
	// (the board, the empty loom, the old dashboard). Anonymous → sign in,
	// then back here.
	let account = $state<AccountRead | null>(null);

	onMount(async () => {
		account = await readAccount();
		if (account.kind === 'anon') window.location.replace(loginFor(ACCOUNT_PATH));
	});
</script>

<svelte:head><title>account · brnrd</title></svelte:head>

<main class="mx-auto max-w-2xl px-5 pt-6 pb-16 sm:px-6" data-testid="account">
	<div class="flex items-baseline justify-between gap-3">
		<div>
			<p class="eyebrow eyebrow--asis">brnrd · account</p>
			<h1 class="mt-2 font-mono text-lg font-semibold text-amber-100">subscription</h1>
		</div>
		<nav class="flex gap-4 font-mono text-[10px] tracking-wide text-ink-quiet uppercase">
			<a href={resolve('/')} class="hover:text-stone-300">board</a>
			<a href={resolve('/pricing')} class="hover:text-stone-300">pricing</a>
		</nav>
	</div>

	{#if account === null || account.kind === 'anon'}
		<p class="mt-4 text-sm text-ink-quiet">reading the account…</p>
	{:else if account.kind === 'error'}
		<p class="mt-4 text-sm text-red-400">{account.message}</p>
	{:else}
		<div class="mt-4">
			<BillingPanel />
		</div>
		<AccountDeletion githubLogin={account.githubLogin} />
	{/if}
</main>
