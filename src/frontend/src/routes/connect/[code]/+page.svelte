<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		ConnectAuthError,
		approvalProofFromHash,
		approveConnect,
		canApprove,
		connectNextUrl,
		fetchConnectContext,
		loginUrlForConnect,
		missingApprovalProof,
		needsRepoEnable,
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
	// The pairing named its own repo (#the-enable-button-that-never-enabled-
	// anything): lead with a one-click "connect <that repo>" instead of the
	// dropdown, which now only shows once the reader deliberately asks for
	// it — e.g. to re-point a daemon at a different repo than its own
	// checkout's remote.
	let showPicker = $state(false);
	let posting = $state(false);
	let result = $state<ApproveResult | null>(null);

	let code = $derived(page.params.code ?? '');
	// The initiator proof the pairing daemon minted, carried here in the URL
	// fragment. The backend refuses an approve without it — a session says
	// who you are, not that you are the one who asked to pair.
	let hash = $derived(page.url.hash ?? '');
	let approveProof = $derived(approvalProofFromHash(hash));
	let linkIncomplete = $derived(context ? missingApprovalProof(context, hash) : false);
	let notice = $derived(context ? statusNotice(context) : null);
	let suggested = $derived(context?.suggested_repo_full_name || '');
	// "local" ⇒ this checkout has no forge behind `owner/name` — the label
	// is synthesized from the folder, not a real GitHub org. Said plainly
	// here rather than letting `local/foo-a1b2c3` read as one.
	let suggestedIsLocal = $derived(context?.suggested_forge === 'local');

	onMount(async () => {
		try {
			context = await fetchConnectContext(code);
			repoId = context.repos[0]?.id ?? '';
			showPicker = context.suggested_repo_full_name === '';
		} catch (e) {
			if (e instanceof ConnectAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'connect context fetch failed';
		}
	});

	// `useSuggested`: approve the pairing's own repo (empty repo_id — the
	// backend binds/creates it) rather than whatever the dropdown currently
	// holds, even though `repoId` may already carry a value from init.
	async function approve(useSuggested: boolean) {
		if (posting) return;
		if (!useSuggested && !repoId) return;
		posting = true;
		result = null;
		try {
			result = await approveConnect(code, useSuggested ? '' : repoId, approveProof);
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
					href={loginUrlForConnect(code, hash)}
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
				Bind pair code <code class="font-mono text-amber-200">{code}</code> to a repository.
			</p>

			{#if notice}
				<p class="mt-4 text-sm text-stone-300">{notice}</p>
				<!-- The one terminal notice with somewhere to go: this pairing
				     named no repo of its own, and this account has nothing
				     connected to fall back to. Every other status here is
				     genuinely terminal and gets no affordance it can't honour. -->
				{#if needsRepoEnable(context)}
					<a
						href={resolve(`/repos?next=${encodeURIComponent(connectNextUrl(code, hash))}`)}
						class="mt-3 inline-flex items-center border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500"
						>connect a repository</a
					>
				{/if}
			{:else if linkIncomplete}
				<!-- A live code, but the link lost its `#…` tail (hand-copied,
				     or forwarded without it). The approve would 403; say so
				     here, where the fix is one paste away. -->
				<p class="mt-4 text-sm text-stone-300">
					This approval link is incomplete. Copy the whole link your terminal printed —
					including everything after the <code class="font-mono text-amber-200">#</code> — and
					open that. It carries the proof that <em>you</em> are the one who asked to pair.
				</p>
			{:else if canApprove(context)}
				{#if suggested && !showPicker}
					<!-- The primary path: the pairing already knows its own repo
					     (parsed from the checkout's git remote), so approving is
					     one click, not a form — running the command locally is
					     the act that connects it, this is just the confirm. -->
					<div class="subpanel mt-4 p-4">
						<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">
							repository
						</p>
						<p class="mt-1 font-mono text-sm text-stone-200">{suggested}</p>
						{#if suggestedIsLocal}
							<p class="mt-1 font-mono text-[10px] tracking-wide text-ink-quiet">
								no forge behind this one — a local checkout, named from its folder
							</p>
						{/if}
						<button
							type="button"
							class="mt-4 cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-not-allowed disabled:border-stone-800 disabled:text-ink-mute"
							disabled={posting}
							onclick={() => approve(true)}
							>{posting ? 'connecting…' : `connect ${suggested}`}</button
						>
						{#if context.repos.length > 0}
							<button
								type="button"
								class="mt-4 ml-3 cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase underline hover:text-stone-300"
								onclick={() => (showPicker = true)}>connect a different repo instead</button
							>
						{/if}
					</div>
				{:else}
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
							onclick={() => approve(false)}>{posting ? 'approving…' : 'approve daemon'}</button
						>
						{#if suggested}
							<button
								type="button"
								class="mt-4 ml-3 cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase underline hover:text-stone-300"
								onclick={() => (showPicker = false)}>back to {suggested}</button
							>
						{/if}
					</div>
				{/if}
			{/if}

			{#if result && !result.ok}
				<p class="mt-3 text-sm text-red-400">{result.notice}</p>
			{/if}
		{/if}
	</section>
</div>
