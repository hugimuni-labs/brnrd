<script lang="ts">
	// The board prompt (threads/web-main/consent.md in the self): a signed-in person whose loom may not yet send its
	// board here answers once, on a page of its own, never over the old app. `ask` and `declined` are answered here;
	// `waiting` is the page between a yes and the loom's first push. The words name what the board part carries
	// (brr.loom.runtime.board: threads, wishes, letters, and each strand's tool lines as tooltrace caps them).
	let {
		step,
		lastSeen = null,
		slow = false,
		busy = false,
		answer
	}: {
		step: 'ask' | 'declined' | 'waiting';
		lastSeen?: string | null;
		slow?: boolean;
		busy?: boolean;
		answer: (send: boolean) => void;
	} = $props();

	const ago = (iso: string | null) => {
		if (!iso) return null;
		const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
		if (s < 90) return 'a minute ago';
		if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
		if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
		return `${Math.round(s / 86400)} days ago`;
	};
</script>

<main class="consent" data-consent={step}>
	{#if step === 'ask'}
		<h1>Show your board here?</h1>
		<p>
			Your resident's board lives on your machine: its threads, wishes, the letters between you and
			it, and what each strand is doing. brnrd.dev can show it to you anywhere, signed in, if your
			loom sends it here.
		</p>
		<p>
			<strong>Sent:</strong> thread titles and summaries, wishes, letter text, strand states, and the
			first line of each step a strand takes (a file path, or the head of a command up to 120 characters,
			secrets masked).
		</p>
		<p><strong>Not sent:</strong> the contents of your files.</p>
		<p>You can stop it any time. Stopping deletes the copy here.</p>
		<div class="acts">
			<button class="yes" disabled={busy} onclick={() => answer(true)}
				>Send my board to brnrd.dev</button
			>
			<button class="no" disabled={busy} onclick={() => answer(false)}>Not now</button>
		</div>
	{:else if step === 'declined'}
		<h1>Your board stays on your machine</h1>
		<p>
			The board needs your loom to send it here, and nothing is sent now. If your loom serves the
			board locally (<code>board_port</code> in its config), it is on that machine at
			<code>127.0.0.1:&lt;board_port&gt;</code>.
		</p>
		<div class="acts">
			<button class="yes" disabled={busy} onclick={() => answer(true)}
				>Send my board after all</button
			>
		</div>
	{:else}
		<h1>Waiting for your loom's first push…</h1>
		<p>
			Your loom sends the board within a minute of the yes; this page turns into it when it lands.
		</p>
		{#if slow}
			<p class="slow">
				{#if lastSeen}
					Your loom was last seen {ago(lastSeen)}. Is it running?
				{:else}
					Your loom has not been seen yet. Is it running?
				{/if}
			</p>
		{/if}
	{/if}
</main>

<style>
	.consent {
		max-width: 36rem;
		margin: 12vh auto;
		padding: 0 1.25rem;
		font-family: ui-monospace, monospace;
		line-height: 1.55;
	}
	h1 {
		font-size: 1.35rem;
		margin: 0 0 1rem;
	}
	p {
		margin: 0 0 0.85rem;
	}
	.acts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		margin-top: 1.5rem;
	}
	button {
		font: inherit;
		padding: 0.55rem 1rem;
		border: 1px solid currentColor;
		background: none;
		color: inherit;
		cursor: pointer;
	}
	button.yes {
		font-weight: 600;
	}
	button:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
