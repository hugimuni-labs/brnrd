<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		ConnectAuthError,
		approveConnect,
		canApprove,
		fetchConnectContext,
		statusNotice,
		type ApproveResult,
		type ConnectContext
	} from '$lib/connect';

	// #327 Jinja-removal, /connect slice — the device-pairing approval page.
	// Every auth consequence stays backend-owned (`approve_core`): session,
	// code expiry, single-use, account-scoped repo lookup. This page renders
	// the handed-back context and relays exactly one click.
	let context = $state<ConnectContext | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let repoId = $state('');
	let posting = $state(false);
	let result = $state<ApproveResult | null>(null);

	let code = $derived(page.params.code ?? '');
	let notice = $derived(context ? statusNotice(context) : null);

	onMount(async () => {
		try {
			context = await fetchConnectContext(code);
			repoId = context.repos[0]?.id ?? '';
		} catch (e) {
			if (e instanceof ConnectAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'connect context fetch failed';
		}
	});

	async function approve() {
		if (!repoId || posting) return;
		posting = true;
		result = null;
		try {
			result = await approveConnect(code, repoId);
		} catch (e) {
			if (e instanceof ConnectAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'approve failed';
		} finally {
			posting = false;
		}
	}
</script>

<svelte:head><title>approve daemon · brnrd</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">pairing handshake</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Approve this daemon
	</h1>

	<section class="panel mt-6 p-5">
		{#if error}
			<p class="text-sm text-red-400">{error}</p>
		{:else if unauthenticated}
			<p class="text-sm text-stone-400">
				Sign in to approve this daemon — <a
					class="text-sky-400 underline"
					href={`/login?next=/connect/${encodeURIComponent(code)}`}
					rel="external">log in</a
				>.
			</p>
		{:else if context === null}
			<p class="text-sm text-ink-quiet">Loading…</p>
		{:else if result?.ok}
			<p class="text-sm text-amber-200">{result.notice}</p>
			{#if result.telegram}
				<div class="subpanel mt-4 p-4">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">telegram</p>
					<p class="mt-1 text-sm text-stone-300">{result.telegram.instructions}</p>
					{#if result.telegram.deep_link}
						<a
							class="mt-3 inline-flex items-center border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500"
							href={result.telegram.deep_link}
							rel="external">Open Telegram and press Start</a
						>
					{/if}
				</div>
			{/if}
		{:else}
			<p class="text-sm text-stone-400">
				Bind pair code <code class="font-mono text-amber-200">{code}</code> to one of your repositories.
			</p>

			{#if notice}
				<p class="mt-4 text-sm text-stone-300">{notice}</p>
			{:else if canApprove(context)}
				<div class="subpanel mt-4 p-4">
					<label
						class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase"
						for="repo_id">repository</label
					>
					<select
						id="repo_id"
						bind:value={repoId}
						class="mt-2 w-full border border-stone-700 bg-stone-950 px-2 py-1.5 font-mono text-sm text-stone-200"
					>
						{#each context.repos as repo (repo.id)}
							<option value={repo.id}>{repo.repo_full_name}</option>
						{/each}
					</select>
					<button
						type="button"
						class="mt-4 cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-not-allowed disabled:border-stone-800 disabled:text-ink-mute"
						disabled={posting || !repoId}
						onclick={approve}>{posting ? 'approving…' : 'approve daemon'}</button
					>
				</div>
			{/if}

			{#if result && !result.ok}
				<p class="mt-3 text-sm text-red-400">{result.notice}</p>
			{/if}
		{/if}
	</section>
</div>
