<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { BASE, probeBoard } from './board/parts';
	import BoardConsent from './BoardConsent.svelte';
	import HomeAccess from './HomeAccess.svelte';
	import { startVisiblePoll } from './visiblePoll';

	const commands = [
		'curl -fsSL https://brnrd.dev/install | sh',
		'export PATH="$HOME/.brnrd/bin:$PATH"',
		'brnrd setup channel --kind relay'
	].join('\n');

	let { board = $bindable() }: { board: 'checking' | 'ready' | 'missing' | 'refused' } = $props();
	let step = $state<'none' | 'ask' | 'declined' | 'granted' | null>(null);
	let lastSeen = $state<string | null>(null);
	let connectionFailed = $state(false);
	let busy = $state(false);
	let failed = $state(false);
	let slow = $state(false);
	let copyStatus = $state('');
	let checking = false;
	let waitingSince = 0;

	async function copy() {
		try {
			await navigator.clipboard.writeText(commands);
			copyStatus = 'Commands copied.';
		} catch {
			copyStatus = 'Could not copy. Select and copy the commands above.';
		}
	}

	async function check(signal?: AbortSignal) {
		if (checking || busy || board === 'ready') return;
		checking = true;
		try {
			const index = await probeBoard(signal);
			if (signal?.aborted) return;
			if (index.kind === 'ready') {
				const justConnected = board === 'missing';
				board = 'ready';
				if (justConnected && !window.location.search && !window.location.hash)
					window.location.assign(resolve('/'));
				return;
			}
			if (index.kind === 'refused') {
				board = 'refused';
				connectionFailed = false;
				return;
			}
			if (index.kind !== 'missing') throw new Error('Board check failed');
			board = 'missing';
			const consent = await fetch(`${BASE}/consent`, { cache: 'no-store', signal });
			if (!consent.ok) throw new Error('Consent check failed');
			const status = await consent.json();
			if (signal?.aborted || busy) return;
			step = status.state;
			lastSeen = status.last_seen_at ?? null;
			if (step === 'granted') {
				waitingSince ||= Date.now();
				slow = Date.now() - waitingSince >= 60_000;
			} else {
				waitingSince = 0;
				slow = false;
			}
			connectionFailed = false;
		} catch {
			if (!signal?.aborted) connectionFailed = true;
		} finally {
			checking = false;
		}
	}

	async function answer(send: boolean) {
		busy = true;
		failed = false;
		const response = await fetch(`${BASE}/consent`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ send })
		}).catch(() => null);
		busy = false;
		if (response?.status === 402) {
			board = 'refused';
			return;
		}
		if (!response?.ok) {
			failed = true;
			return;
		}
		step = send ? 'granted' : 'declined';
		void check();
	}

	onMount(() => startVisiblePoll(check, document, 3000));
</script>

<HomeAccess refused={board === 'refused'} />

{#if board === 'missing' && step === 'none'}
	<section id="setup" class="mb-6 space-y-3" aria-label="Set up brnrd">
		<h2>Set up brnrd</h2>
		<p>
			Open Terminal on the Mac or Linux computer where you want brnrd to run. Sign in to your coding
			agent there first, then run these commands.
		</p>
		<pre class="wrap-break-word whitespace-pre-wrap">{commands}</pre>
		<button type="button" class="border" onclick={copy}>Copy commands</button>
		<p role="status">{copyStatus}</p>
		<p>
			Open the approval link from Terminal, approve this computer, and choose to show your board
			here.
		</p>
	</section>
{:else if board === 'missing' && step !== null && step !== 'none'}
	<BoardConsent
		step={step === 'granted' ? 'waiting' : step}
		{lastSeen}
		{slow}
		{busy}
		{failed}
		{answer}
		embedded
	/>
{/if}

{#if (board === 'missing' && step === 'none') || connectionFailed}
	<div class="mb-6 flex flex-wrap items-center gap-3">
		<p role="status">
			{connectionFailed
				? 'Could not check for your computer.'
				: 'Waiting for your computer to connect.'}
		</p>
		<button type="button" class="border" onclick={() => void check()}>Check again</button>
	</div>
{/if}
