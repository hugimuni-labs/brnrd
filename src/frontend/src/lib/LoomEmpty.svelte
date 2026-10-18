<script lang="ts">
	// Signed in, nothing running yet. One screen: the three nouns the board
	// is made of, then the one step that makes it fill.
	import { resolve } from '$app/paths';

	const NOUNS = [
		{
			word: 'thread',
			glyph: '═',
			line: 'A piece of work with its own plan. It stays open until it is done.'
		},
		{
			word: 'strand',
			glyph: '↯',
			line: 'A body working a thread: a model on a shell, with a live state.'
		},
		{
			word: 'letter',
			glyph: '✉',
			line: 'What you and the strands send each other. Receipts are letters too.'
		}
	];

	let copied = $state(false);
	async function copy() {
		try {
			await navigator.clipboard.writeText('npm install -g brnrd\nbrnrd account connect');
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			// No clipboard: the command is still there to select by hand.
		}
	}
</script>

<main class="mx-auto max-w-2xl px-5 pt-6 pb-16 sm:px-6" data-testid="loom-empty">
	<p class="eyebrow eyebrow--asis">brnrd · the loom</p>
	<h1 class="mt-2 font-mono text-lg font-semibold text-amber-100">nothing on the loom yet</h1>
	<p class="mt-1 text-sm text-stone-400">
		This board shows the loom running on your own machine. Three words are all it uses.
	</p>

	<ol class="mt-5 space-y-2">
		{#each NOUNS as noun, i (noun.word)}
			<li class="subpanel ignite flex gap-3 p-3" style={`--ignite-delay: ${60 + i * 60}ms`}>
				<span
					class="w-5 shrink-0 text-center font-mono text-lg text-amber-300/90"
					aria-hidden="true">{noun.glyph}</span
				>
				<div>
					<p class="font-mono text-sm text-stone-100">{noun.word}</p>
					<p class="mt-0.5 text-sm text-stone-400">{noun.line}</p>
				</div>
			</li>
		{/each}
	</ol>

	<section class="panel ignite mt-5 p-4" style="--ignite-delay: 260ms">
		<p class="eyebrow">start it</p>
		<pre class="mt-2 overflow-x-auto font-mono text-sm leading-relaxed text-amber-100"><span
				class="text-ink-mute"
				>$ </span>npm install -g brnrd
<span class="text-ink-mute">$ </span>brnrd account connect</pre>
		<div class="mt-3 flex items-center gap-3 font-mono text-[11px]">
			<button
				type="button"
				class="border border-amber-600/70 px-2.5 py-1 text-amber-100 hover:bg-amber-950/40"
				onclick={copy}>{copied ? 'copied' : 'copy'}</button
			>
			<span class="text-ink-quiet">your first letter goes to inbox; it opens threads for you</span>
		</div>
	</section>

	<p class="mt-4 font-mono text-[10px] text-ink-mute">
		already set up another way? <a
			href={resolve('/bridge')}
			class="text-ink-quiet hover:text-amber-200">the bridge →</a
		>
	</p>
</main>
