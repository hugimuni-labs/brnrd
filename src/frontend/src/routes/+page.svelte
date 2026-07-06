<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import WindowTrack from '$lib/WindowTrack.svelte';
	import { QuotaAuthError, fetchQuota, type QuotaShell } from '$lib/quota';

	// Slice 2 (kb/design-dashboard-live-surface.md): the window-track
	// live-quota view. Polls the same daemon-published data the Jinja
	// dashboard's quota card reads (`GET /v1/dashboard/quota`), so the two
	// surfaces agree until the Jinja one is retired.
	const POLL_MS = 20_000;
	const TICK_MS = 1_000;

	let shells = $state<QuotaShell[] | null>(null);
	let generatedAt = $state<string | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let now = $state(Date.now());

	let pollHandle: ReturnType<typeof setInterval> | undefined;
	let tickHandle: ReturnType<typeof setInterval> | undefined;

	async function refresh() {
		try {
			const data = await fetchQuota();
			shells = data.runner_quotas;
			generatedAt = data.generated_at;
			error = null;
			unauthenticated = false;
		} catch (e) {
			if (e instanceof QuotaAuthError) {
				unauthenticated = true;
			} else {
				error = e instanceof Error ? e.message : 'quota fetch failed';
			}
		}
	}

	onMount(() => {
		refresh();
		pollHandle = setInterval(refresh, POLL_MS);
		tickHandle = setInterval(() => {
			now = Date.now();
		}, TICK_MS);
	});

	onDestroy(() => {
		if (pollHandle) clearInterval(pollHandle);
		if (tickHandle) clearInterval(tickHandle);
	});
</script>

<div class="mx-auto max-w-2xl p-6">
	<h1 class="text-2xl font-semibold text-slate-100">brnrd dashboard — next</h1>
	<p class="mt-2 text-sm text-slate-400">
		Live per-shell quota windows — the first real screen on the new stack. See
		<code>kb/design-dashboard-live-surface.md</code> in the main repo for the fuller live-flow plan this
		is slice 2 of.
	</p>

	<div class="mt-6 space-y-3">
		{#if unauthenticated}
			<p class="text-sm text-slate-400">
				Sign in to see live quota windows — <a
					class="text-blue-400 underline"
					href="/login?next=/"
					rel="external">log in</a
				>.
			</p>
		{:else if error}
			<p class="text-sm text-red-400">{error}</p>
		{:else if shells === null}
			<p class="text-sm text-slate-500">Loading…</p>
		{:else if shells.length === 0}
			<p class="text-sm text-slate-500">No connected daemon has reported quota yet.</p>
		{:else}
			{#each shells as shell (shell.shell)}
				<WindowTrack {shell} {now} />
			{/each}
			{#if generatedAt}
				<p class="text-right text-[11px] text-slate-600">
					daemon report as of {new Date(generatedAt).toLocaleTimeString()}
				</p>
			{/if}
		{/if}
	</div>
</div>
