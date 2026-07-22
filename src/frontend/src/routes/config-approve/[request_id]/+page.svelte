<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		canDecide,
		ConfigApproveAuthError,
		decideConfigApproveRequest,
		fetchConfigApproveRequest,
		statusNotice,
		type ConfigApproveRequest
	} from '$lib/configApprove';

	let request = $state<ConfigApproveRequest | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let posting = $state(false);
	let result = $state<string | null>(null);

	let requestId = $derived(page.params.request_id ?? '');
	let notice = $derived(request ? statusNotice(request) : null);

	onMount(async () => {
		try {
			request = await fetchConfigApproveRequest(requestId);
		} catch (e) {
			if (e instanceof ConfigApproveAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'config approval fetch failed';
		}
	});

	async function decide(decision: 'approve' | 'reject') {
		if (!request || !canDecide(request) || posting) return;
		posting = true;
		result = null;
		try {
			const response = await decideConfigApproveRequest(requestId, decision);
			if (response.request) request = response.request;
			if (response.ok) result = response.notice;
			else error = response.notice;
		} catch (e) {
			if (e instanceof ConfigApproveAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'could not record a decision';
		} finally {
			posting = false;
		}
	}
</script>

<svelte:head><title>approve config change · brnrd</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">config-change request</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Approve config change
	</h1>

	<section class="panel mt-6 p-5">
		{#if error}
			<p class="text-sm text-red-400">{error}</p>
		{:else if unauthenticated}
			<p class="text-sm text-stone-400">
				Sign in to review this config change — <a
					class="text-sky-400 underline"
					href={`/login?next=/config-approve/${encodeURIComponent(requestId)}`}
					rel="external">log in</a
				>.
			</p>
		{:else if request === null}
			<p class="text-sm text-ink-quiet">Loading…</p>
		{:else}
			<p class="text-sm text-stone-400">
				Your resident on <strong>{request.repo_label}</strong> wants to change a limit you control.
			</p>
			<dl class="mt-5 grid grid-cols-[auto_1fr] gap-x-5 gap-y-3 text-sm">
				<dt class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">key</dt>
				<dd><code>{request.config_key}</code></dd>
				<dt class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">current</dt>
				<dd><code>{request.current_value || '(unset)'}</code></dd>
				<dt class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">requested</dt>
				<dd><code>{request.requested_value}</code></dd>
				{#if request.reason}<dt
						class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase"
					>
						reason
					</dt>
					<dd>{request.reason}</dd>{/if}
			</dl>

			{#if result}<p class="mt-5 text-sm text-amber-200">{result}</p>{:else if notice}<p
					class="mt-5 text-sm text-stone-300"
				>
					{notice}
				</p>{:else if canDecide(request)}
				<div class="mt-5 flex gap-3">
					<button
						type="button"
						class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-not-allowed"
						disabled={posting}
						onclick={() => decide('approve')}>{posting ? 'recording…' : 'approve'}</button
					>
					<button
						type="button"
						class="cursor-pointer border border-stone-700 px-3 py-1.5 font-mono text-[11px] tracking-wide text-stone-300 uppercase hover:border-stone-500 disabled:cursor-not-allowed"
						disabled={posting}
						onclick={() => decide('reject')}>reject</button
					>
				</div>
			{/if}
		{/if}
	</section>
</div>
