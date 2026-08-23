<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		ConnectAuthError,
		approvalProofFromHash,
		approveConnect,
		canApprove,
		codeInFlight,
		connectPhase,
		connectNextUrl,
		fetchConnectContext,
		loginUrlForConnect,
		missingApprovalProof,
		needsRepoEnable,
		statusNotice,
		type ApproveResult,
		type ConnectContext
	} from '$lib/connect';
	import MessengerDoors from '$lib/MessengerDoors.svelte';
	import { fetchRepos, type MessengerDoor } from '$lib/repos';

	// The merged single-screen connect flow. Handles three phases in-place:
	//
	//   entry   — BR- code input form (/connect, or reload before submit)
	//   confirm — "Pairing code accepted" + repo + approve button
	//   done    — connected notice + MessengerDoors
	//
	// Deep-link entry (/connect/<code>#<code>) starts at confirm directly;
	// the [code] route passes initialCode + initialHash. When both are absent,
	// the component starts at entry (/connect route).
	//
	// URL is updated to /connect/<code>#<code> on phase-1 submit so a reload
	// lands on a working approval page — history.replaceState, not a navigate.

	interface Props {
		// Code from the URL (deep-link / [code] route). Empty ⇒ start at entry.
		initialCode?: string;
		// Hash from the URL (the `#BR-…` fragment). Only meaningful alongside initialCode.
		initialHash?: string;
	}
	let { initialCode = '', initialHash = '' }: Props = $props();

	type Phase = 'entry' | 'confirm' | 'done';

	// The code in flight has two possible owners and they never overlap: the
	// URL owns it on `/connect/<code>` (this component never submits a form
	// there), and the form owns it on `/connect` (where the prop stays `''`
	// for the component's whole life, because phase 2 arrives by
	// `replaceState`, not by navigation).
	//
	// Derived, not copied, and that is not style. Copying a prop into `$state`
	// captures its *initial* value — the compiler names it,
	// `state_referenced_locally` — and SvelteKit keeps this component mounted
	// across `/connect/A` -> `/connect/B`, so a copied `code` would hold A
	// while the reader looks at B and the approve would post the wrong one.
	// The warning went unheard upstream because the authoring worktree had no
	// `.svelte-kit/`, so `svelte-check` failed on every file at once and its
	// output read as environment noise.
	let submitted = $state<{ code: string; hash: string } | null>(null);
	let approved = $state(false);

	let code = $derived(codeInFlight(submitted, initialCode));
	// The URL fragment carrying the initiator proof
	let hash = $derived(submitted?.hash ?? initialHash);
	let phase = $derived<Phase>(connectPhase({ code, approved }));

	// --- entry phase state ---
	let enteredCode = $state('');
	let entryError = $state<string | null>(null);
	let submitting = $state(false);

	// --- confirm phase state ---
	let context = $state<ConnectContext | null>(null);
	let contextError = $state<string | null>(null);
	let unauthenticated = $state(false);
	let showPicker = $state(false);
	let repoId = $state('');
	let posting = $state(false);
	let result = $state<ApproveResult | null>(null);

	// --- done phase state ---
	let messengerDoors = $state<MessengerDoor[] | null>(null);

	// Derived from context + hash, same semantics as [code]/+page.svelte
	let approveProof = $derived(approvalProofFromHash(hash));
	let linkIncomplete = $derived(context ? missingApprovalProof(context, hash) : false);
	let notice = $derived(context ? statusNotice(context) : null);
	let suggested = $derived(context?.suggested_repo_full_name || '');
	let suggestedIsLocal = $derived(context?.suggested_forge === 'local');

	async function loadContext(c: string): Promise<void> {
		contextError = null;
		unauthenticated = false;
		context = null;
		try {
			context = await fetchConnectContext(c);
			repoId = context.repos[0]?.id ?? '';
			showPicker = context.suggested_repo_full_name === '';
		} catch (e) {
			if (e instanceof ConnectAuthError) unauthenticated = true;
			else contextError = e instanceof Error ? e.message : 'connect context fetch failed';
		}
	}

	// One fetch per code, whichever owner supplied it — deep-link mount, form
	// submit, or a navigation that swapped the prop under a mounted component.
	// `onMount` covered only the first of those three.
	$effect(() => {
		const inFlight = code;
		if (inFlight) loadContext(inFlight);
	});

	// SvelteKit keeps this component mounted across `/connect/A` → `/connect/B`,
	// so the props change while `code`/`hash` still hold A's values and
	// `onMount` never fires again — the approve would post the previous code.
	// The compiler says so out loud (`state_referenced_locally`); it went
	// unheard because the strand's worktree had no `.svelte-kit/`, so
	// `svelte-check` failed on every file and its warnings were read as
	// environment. Never fires on mount: `code` is initialised to
	// `initialCode`, so the guard is false exactly once.
	$effect(() => {
		if (initialCode && initialCode !== code) {
			code = initialCode;
			hash = initialHash;
			phase = 'confirm';
			result = null;
			showPicker = false;
			loadContext(initialCode);
		}
	});

	function normalize(raw: string): string {
		return raw.trim().toUpperCase().replace(/\s+/g, '');
	}

	async function submitCode(): Promise<void> {
		const entered = normalize(enteredCode);
		if (!/^BR-[A-Z2-9]{8}$/.test(entered)) {
			entryError = 'Enter the BR- code printed in your terminal.';
			return;
		}
		entryError = null;
		submitting = true;
		// Update the URL so a reload lands on a working approval page rather than
		// an empty form. replaceState (not pushState / assign) — this is not a
		// navigation, just a bookmark for the session.
		history.replaceState(null, '', resolve(`/connect/${entered}`) + '#' + entered);
		// The one-time device code is also the initiator proof — carry it in the
		// fragment so login detours preserve it without sending it to a server.
		// This single write moves the phase and triggers the fetch; the effect
		// above owns the request.
		submitted = { code: entered, hash: '#' + entered };
		submitting = false;
	}

	// `useSuggested`: approve with the pairing's own repo (empty repo_id —
	// backend binds/creates it) rather than the dropdown's current selection.
	async function approve(useSuggested: boolean): Promise<void> {
		if (posting) return;
		if (!useSuggested && !repoId) return;
		posting = true;
		result = null;
		try {
			result = await approveConnect(code, useSuggested ? '' : repoId, approveProof);
			if (result.ok) {
				try {
					const repos = await fetchRepos();
					messengerDoors = repos.messenger_doors ?? null;
				} catch {
					messengerDoors = null;
				}
				approved = true;
			}
		} catch (e) {
			if (e instanceof ConnectAuthError) unauthenticated = true;
			else contextError = e instanceof Error ? e.message : 'approve failed';
		} finally {
			posting = false;
		}
	}
</script>

<svelte:head>
	<title
		>{phase === 'done'
			? 'daemon connected'
			: phase === 'confirm'
				? 'approve daemon'
				: 'connect daemon'} · brnrd</title
	>
</svelte:head>

<!-- The done phase renders the two-column messenger-door grid; the entry and
     confirm phases are a single column and read better narrow. -->
<div class="mx-auto p-6 {phase === 'done' ? 'max-w-2xl' : 'max-w-xl'}">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">pairing handshake</p>
		{#if phase !== 'entry'}
			<a
				href={resolve('/')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>dashboard</a
			>
		{/if}
	</div>
	<!-- The heading does not change between entry and confirm, deliberately.
	     A panel whose title renames itself under the reader is a second
	     screen wearing the first one's URL — and the second screen is the
	     thing this flow exists to have deleted. -->
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		{phase === 'done' ? 'Daemon connected' : 'Connect your daemon'}
	</h1>

	<section class="panel mt-6 p-5">
		{#if phase === 'entry'}
			<!-- Phase 1: code entry form -->
			<p class="text-sm text-stone-300">
				Run <code class="font-mono text-amber-200">brnrd account connect</code> in your checkout, then
				enter the one-time code it prints.
			</p>
			<form
				class="mt-5"
				onsubmit={(event) => {
					event.preventDefault();
					submitCode();
				}}
			>
				<label
					class="font-mono text-[11px] tracking-wide text-amber-200/80 uppercase"
					for="pair-code"
				>
					pairing code
				</label>
				<input
					id="pair-code"
					bind:value={enteredCode}
					class="mt-2 w-full border border-stone-700 bg-stone-950 px-3 py-3 font-mono text-lg tracking-wider text-stone-100 uppercase"
					placeholder="BR-XXXXXXXX"
					autocomplete="one-time-code"
					spellcheck="false"
				/>
				<button
					type="submit"
					disabled={submitting}
					class="mt-4 cursor-pointer border border-sky-700 bg-sky-950/40 px-4 py-2 font-mono text-sm tracking-wide text-sky-100 uppercase hover:border-sky-500 disabled:cursor-not-allowed disabled:border-stone-800 disabled:text-ink-mute"
					>continue</button
				>
			</form>
			{#if entryError}<p class="mt-3 text-sm text-red-400">{entryError}</p>{/if}
		{:else if phase === 'confirm'}
			<!-- Phase 2 sits *below* phase 1's own field rather than replacing
			     it: the code stays on screen, accepted, and the repository
			     appears under it. One panel that grows, not two panels that
			     swap. -->
			<div class="flex items-baseline justify-between gap-3">
				<p class="font-mono text-[11px] tracking-wide text-amber-200/80 uppercase">pairing code</p>
				<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">accepted</p>
			</div>
			<p
				class="mt-2 w-full border border-stone-800 bg-stone-950/60 px-3 py-3 font-mono text-lg tracking-wider text-ink-quiet"
				data-testid="accepted-code"
			>
				{code}
			</p>

			{#if contextError}
				<p class="text-sm text-red-400">{contextError}</p>
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
				<!-- Shouldn't reach here — result.ok transitions to 'done' — guard only -->
				<p class="text-sm text-amber-200">{result.notice}</p>
			{:else}
				<p class="mt-4 text-sm text-stone-400">
					{suggested && !showPicker
						? 'This is the repository your checkout named. Connect it and the terminal takes over from there.'
						: 'Choose the repository this daemon should serve.'}
				</p>

				{#if notice}
					<p class="mt-4 text-sm text-stone-300">{notice}</p>
					<!-- The one terminal notice with somewhere to go: this pairing
					     named no repo, and the account has nothing to fall back to.
					     Every other status here is genuinely terminal. -->
					{#if needsRepoEnable(context)}
						<a
							href={resolve(`/repos?next=${encodeURIComponent(connectNextUrl(code, hash))}`)}
							class="mt-3 inline-flex items-center border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500"
							>connect a repository</a
						>
					{/if}
				{:else if linkIncomplete}
					<!-- A live code but the link lost its #… tail. -->
					<p class="mt-4 text-sm text-stone-300">
						This approval link is incomplete. Copy the whole link your terminal printed — including
						everything after the <code class="font-mono text-amber-200">#</code> — and open that. It
						carries the proof that <em>you</em> are the one who asked to pair.
					</p>
				{:else if canApprove(context)}
					{#if suggested && !showPicker}
						<!-- Primary path: the pairing already knows its repo. -->
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
		{:else}
			<!-- Phase 3: done -->
			<p class="text-sm text-amber-200">{result?.notice ?? 'Your daemon is connected.'}</p>
			<MessengerDoors doors={messengerDoors} heading="continue in another chat" />
		{/if}
	</section>
</div>
