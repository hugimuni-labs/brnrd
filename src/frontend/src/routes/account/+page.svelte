<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import AccountDeletion from '$lib/AccountDeletion.svelte';
	import BillingPanel from '$lib/BillingPanel.svelte';
	import AccountSetup from '$lib/AccountSetup.svelte';
	import {
		ACCOUNT_PATH,
		githubReturn,
		loginFor,
		readAccount,
		type AccountRead
	} from '$lib/accountPage';
	import '$lib/board/ui/board.css';
	import { forget } from '$lib/board/cache';

	// It wears the board's look (his 11408: the old style was the dashboard's
	// dark panels): the board's header bar, one column with its top rule, mono
	// ink on white. BillingPanel and AccountDeletion are shared with the old
	// dashboard and keep its classes; the skin below restyles them here only.
	let account = $state<AccountRead | null>(null);
	let board = $state<'checking' | 'ready' | 'missing' | 'refused'>('checking');
	async function signOut(event: MouseEvent) {
		event.preventDefault();
		await forget();
		window.location.assign('/logout');
	}

	onMount(() => {
		// the board's page drops the layout's dark scanline overlay the same way
		document.documentElement.classList.add('board');
		const back = githubReturn(window.location.search);
		void readAccount(fetch, back).then((a) => {
			account = a;
			// Signed out, the GitHub return comes back here with its params after sign-in.
			if (a.kind === 'anon')
				window.location.replace(loginFor(ACCOUNT_PATH + window.location.search));
			// Read once: a reload neither re-syncs nor re-announces.
			else if (back.notice || back.installationId)
				history.replaceState(history.state, '', ACCOUNT_PATH + window.location.hash);
		});
		return () => document.documentElement.classList.remove('board');
	});
</script>

<svelte:head><title>account · brnrd</title></svelte:head>

<div class="brd acct" data-testid="account">
	<header class="flex-wrap">
		{#if board === 'ready'}
			<a class="topics-link" href={resolve('/')}>← board</a>
		{:else if board === 'missing'}
			<span class="dim">Your board appears here once a computer connects.</span>
		{/if}
		<b>account</b>
		<a class="topics-link" href={resolve('/pricing')}>pricing</a>
		{#if account?.kind === 'authed' && account.githubLogin}
			<span class="dim">@{account.githubLogin}</span>
			<a class="topics-link" href={resolve('/login?switch=1&next=%2Faccount')}
				>Switch GitHub account</a
			>
			<a class="topics-link" href="/logout" rel="external" onclick={signOut}>Sign out</a>
		{/if}
	</header>
	<main>
		<section class="sheet">
			{#if account === null || account.kind === 'anon'}
				<p class="dim">reading the account…</p>
			{:else if account.kind === 'error'}
				<p class="warn">{account.message}</p>
			{:else}
				<AccountSetup bind:board />
				{#if account.github.notice || account.github.repos.length}
					<section id="github" class="github" aria-label="GitHub">
						<h2>GitHub</h2>
						{#if account.github.notice}
							<p class="subpanel note" role="status">{account.github.notice}</p>
						{/if}
						{#if account.github.repos.length}
							<p>The brnrd-dev GitHub App reaches:</p>
							<ul>
								{#each account.github.repos as repo (repo)}
									<li><code>{repo}</code></li>
								{/each}
							</ul>
						{/if}
						{#if account.github.installUrl}
							<p>
								<!-- eslint-disable svelte/no-navigation-without-resolve -- GitHub's own install page -->
								<a class="topics-link" href={account.github.installUrl} rel="external"
									>Choose which repos the App reaches</a
								>
								<!-- eslint-enable svelte/no-navigation-without-resolve -->
							</p>
						{/if}
					</section>
				{/if}
				<h2 id="subscription">Subscription</h2>
				<BillingPanel />
				<AccountDeletion githubLogin={account.githubLogin} />
			{/if}
		</section>
	</main>
</div>

<style>
	.acct header {
		height: auto;
		min-height: 40px;
	}

	.acct main {
		flex: 1;
		overflow-y: auto;
		background: #fafafa;
	}
	.sheet {
		max-width: 640px;
		margin: 0 auto;
		min-height: 100%;
		padding: 14px 18px 40px;
		background: #fff;
		border-left: 1px solid #aaa;
		border-right: 1px solid #aaa;
		border-top: 5px solid var(--amber);
	}
	@media (max-width: 799px) {
		.sheet {
			border-left: 0;
			border-right: 0;
		}
	}

	/* The shared panels in the board's ink: no brackets or glow, mono at the board's sizes, the board's buttons. */
	.acct :global(.panel) {
		padding: 0;
		border: 0;
		background: none;
		box-shadow: none;
		animation: none;
	}
	.acct :global(.subpanel) {
		border: 1px solid #ddd;
		border-left: 3px solid var(--amber);
		background: #fffaf0;
	}
	.acct :global(p),
	.acct :global(label),
	.acct :global(strong),
	.acct :global(input) {
		font-family: inherit;
	}
	.acct :global(.text-sm) {
		font-size: 12px;
		line-height: 1.45;
	}
	.acct :global(.text-amber-100),
	.acct :global(.text-amber-200),
	.acct :global(.text-stone-200),
	.acct :global(.text-stone-300) {
		color: #171717;
	}
	.acct :global(.text-stone-400) {
		color: #555;
	}
	.acct :global(.text-ink-quiet) {
		color: #777;
	}
	.acct :global(.text-red-400) {
		color: #b00;
	}
	.acct :global(button) {
		font: inherit;
		font-size: 11px;
		letter-spacing: 0;
		text-transform: none;
		line-height: 20px;
		padding: 1px 10px;
		background: none;
		color: #171717;
		border: 1px solid #bbb;
		cursor: pointer;
	}
	.acct :global(button:hover:not(:disabled)) {
		background: none;
		border-color: #333;
	}
	.acct :global(button:disabled) {
		opacity: 0.3;
		cursor: default;
	}
	/* the act the panel exists for (subscribe, resume): the board's filled `done` button */
	.acct :global(button.border-amber-700) {
		background: var(--ember);
		border-color: var(--ember);
		color: #fff;
		font-weight: 600;
	}
	.acct :global(button.border-amber-700:hover:not(:disabled)) {
		background: #c94a20;
		border-color: #c94a20;
	}
	.acct :global(button.border-red-800) {
		border-color: #b00;
		color: #b00;
	}
	.acct :global(button.border-red-800:hover:not(:disabled)) {
		background: #b00;
		border-color: #b00;
		color: #fff;
	}
	.acct :global(button:not([class*='border'])) {
		border-color: transparent;
	}
	.acct :global(input) {
		background: #fff;
		color: #171717;
		border: 1px solid #bbb;
		font-size: 12px;
	}
	.acct :global(input:focus) {
		border-color: #b00;
	}
	.github {
		margin: 0 0 1.5rem;
	}
	.github .note {
		padding: 6px 10px;
	}
	.github ul {
		margin: 0.25rem 0 0.75rem;
		padding-left: 1.25rem;
	}
	/* the deletion block: a rule above, as the board separates a column's parts */
	.acct :global(.border-stone-800) {
		border: 0;
		border-top: 1px solid #ccc;
		padding: 14px 0 0;
		margin-top: 28px;
	}
</style>
