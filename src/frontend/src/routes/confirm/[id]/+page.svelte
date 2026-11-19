<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { loadSite } from '$lib/site';
	import { onMount } from 'svelte';

	type Confirmation = {
		request: { id: string; what: string; thread: string; digest: string; ask_exp: string };
		call: { command?: string; tool?: string; input?: unknown };
		why?: string;
		status: string;
		pressed_at?: string;
		can_press: boolean;
		cloud?: string;
	};

	let confirmation = $state<Confirmation>();
	let error = $state('');
	let needsLogin = $state(false);
	let busy = $state(false);
	let endpoint = '';
	const format = (value: string) =>
		new Date(value).toLocaleString(undefined, {
			dateStyle: 'medium',
			timeStyle: 'short'
		});

	async function update(decision?: 'approve' | 'decline') {
		busy = true;
		error = '';
		try {
			const response = await fetch(
				endpoint,
				decision && {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ decision })
				}
			);
			needsLogin = response.status === 401;
			const data = await response.json();
			if (!response.ok)
				throw new Error(data.detail ?? data.error ?? 'Could not load confirmation.');
			confirmation = data;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Could not reach the dashboard.';
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		const id = encodeURIComponent(page.params.id ?? '');
		void loadSite().then((site) => {
			endpoint =
				site?.deployment === 'loopback'
					? `/v1/loom/confirms/${id}`
					: `/v1/dashboard/loom/confirms/${id}`;
			void update();
		});
	});
</script>

<svelte:head>
	<title>Confirm an action · brnrd</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<main>
	{#if confirmation}
		<p>brnrd asks first, because this cannot be undone</p>
		<h1>{confirmation.request.what}</h1>
		{#if confirmation.why}<p>Why: {confirmation.why}</p>{/if}
		<p>Until {format(confirmation.request.ask_exp)}</p>
		<details>
			<summary>Exact call</summary>
			<dl>
				<dt>Mark</dt>
				<dd>final · {confirmation.request.what} · confirm · none</dd>
				<dt>Thread</dt>
				<dd>{confirmation.request.thread}</dd>
				<dt>Digest</dt>
				<dd>{confirmation.request.digest}</dd>
			</dl>
			<pre>{confirmation.call.command ?? JSON.stringify(confirmation.call, null, 2)}</pre>
		</details>

		{#if confirmation.status === 'given' || confirmation.status === 'used'}
			<p role="status">
				Done: you said do it{confirmation.pressed_at ? `, ${format(confirmation.pressed_at)}` : ''}
			</p>
		{:else if confirmation.status === 'refused'}
			<p role="status">You said don’t.</p>
		{:else if confirmation.status === 'expired'}
			<p role="status">This request has expired.</p>
		{:else if confirmation.can_press}
			<div class="actions">
				<button disabled={busy} onclick={() => update('approve')}>Do it</button>
				<button disabled={busy} onclick={() => update('decline')}>Don’t</button>
			</div>
		{:else if confirmation.cloud}
			<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- configured dashboard origin -->
			<p><a href={confirmation.cloud}>Open the cloud dashboard to answer</a></p>
		{/if}
	{:else if busy}
		<p role="status">Loading confirmation…</p>
	{/if}
	{#if error}<p role="alert">{error}</p>{/if}
	{#if needsLogin}
		<a href={resolve(`/login?next=${encodeURIComponent(page.url.pathname)}`)}>Sign in</a>
	{/if}
</main>

<style>
	main {
		max-width: 42rem;
		margin: 4rem auto;
		padding: 0 1.5rem;
	}
	h1 {
		font-size: clamp(1.6rem, 5vw, 2.5rem);
		line-height: 1.2;
		margin: 0.5rem 0 1rem;
	}
	p,
	details {
		margin-bottom: 1rem;
	}
	summary {
		cursor: pointer;
	}
	dt {
		font-weight: 600;
	}
	dd,
	pre {
		overflow-wrap: anywhere;
	}
	pre {
		white-space: pre-wrap;
	}
	.actions {
		display: flex;
		gap: 0.75rem;
	}
	button {
		border: 1px solid currentColor;
		border-radius: 0.375rem;
		padding: 0.55rem 1rem;
		cursor: pointer;
	}
	button:disabled {
		cursor: wait;
		opacity: 0.6;
	}
	a {
		text-decoration: underline;
	}
</style>
