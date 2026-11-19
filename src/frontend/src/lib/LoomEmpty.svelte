<script lang="ts">
	import { resolve } from '$app/paths';

	let {
		retry = undefined,
		connectionFailed = false
	}: {
		retry?: () => void;
		connectionFailed?: boolean;
	} = $props();

	const commands = [
		'curl -fsSL https://brnrd.dev/install | sh',
		'export PATH="$HOME/.brnrd/bin:$PATH"',
		'brnrd setup channel --kind relay'
	].join('\n');

	const NOUNS = [
		{
			word: 'thread',
			line: 'A piece of work with its own plan. It stays open until it is done.'
		},
		{
			word: 'strand',
			line: 'A body working a thread: a model on a shell, with a live state.'
		},
		{
			word: 'letter',
			line: 'What you and the strands send each other. Receipts are letters too.'
		}
	];

	let copyStatus = $state('');
	async function copy() {
		try {
			await navigator.clipboard.writeText(commands);
			copyStatus = 'Commands copied.';
		} catch {
			copyStatus = 'Could not copy. Select and copy the commands above.';
		}
	}
</script>

<main class="mx-auto max-w-2xl px-5 pt-6 pb-16 sm:px-6" data-testid="loom-empty">
	<header class="flex items-baseline justify-between gap-3">
		<p class="eyebrow eyebrow--asis">brnrd</p>
		<nav aria-label="Account" class="flex gap-4 text-sm text-stone-400">
			<a href={resolve('/account')} class="hover:text-amber-200">Account</a>
			<a href="/logout" rel="external" class="hover:text-amber-200">Sign out</a>
		</nav>
	</header>
	<h1 class="mt-5 text-xl font-semibold text-amber-100">Set up brnrd</h1>
	<p class="mt-2 text-sm text-stone-400">
		Open Terminal on the Mac or Linux computer where you want brnrd to run. Sign in to your coding
		agent there first, then run these commands.
	</p>

	<section class="panel mt-4 p-4" aria-label="Install commands">
		<pre
			class="font-mono text-sm leading-relaxed wrap-break-word whitespace-pre-wrap text-amber-100">{commands}</pre>
		<button
			type="button"
			class="mt-3 border border-amber-600/70 px-2.5 py-1 text-sm text-amber-100 hover:bg-amber-950/40"
			onclick={copy}>Copy commands</button
		>
		<p class="mt-2 text-sm text-stone-400" role="status">{copyStatus}</p>
	</section>

	<ol class="mt-5 list-decimal space-y-2 ps-5 text-sm text-stone-400">
		<li>Install brnrd in Terminal.</li>
		<li>Open the approval link from Terminal and approve the computer with this account.</li>
		<li>Choose to show your board here. This page checks for your computer automatically.</li>
	</ol>
	{#if retry}
		<div class="mt-3 flex flex-wrap items-center gap-3 text-sm">
			<p role="status" class="text-stone-400">
				{connectionFailed
					? 'Could not check for your computer.'
					: 'Waiting for your computer to connect.'}
			</p>
			<button type="button" class="text-amber-200 underline" onclick={retry}>Check again</button>
		</div>
	{/if}

	<details class="mt-5 text-sm text-stone-400">
		<summary class="cursor-pointer">Other install options and board terms</summary>
		<p class="mt-2">
			You can also install with <code>npm install -g brnrd</code>, then run the setup command above.
		</p>
		<dl class="mt-2 space-y-2">
			{#each NOUNS as noun (noun.word)}
				<div>
					<dt class="text-stone-100">{noun.word}</dt>
					<dd>{noun.line}</dd>
				</div>
			{/each}
		</dl>
		<a href={resolve('/dashboard/bridge')} class="mt-2 inline-block text-amber-200 underline">
			Open the older dashboard
		</a>
	</details>
</main>
