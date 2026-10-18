<script lang="ts">
	// Write a letter. It goes to the hosted inbox the relay drains, so a 202
	// means queued, not read: the composer says exactly that. The body comes
	// from the fuel strip; the thread is inbox unless you pick another (or the
	// page already is one thread).
	import { LoomAuthError, postLetter, type LoomThread } from './loomSnapshot.ts';

	let {
		threads,
		thread = $bindable('inbox'),
		body,
		fixed = false
	}: { threads: LoomThread[]; thread?: string; body: string | null; fixed?: boolean } = $props();

	let text = $state('');
	let sending = $state(false);
	let outcome = $state<{ tone: 'ok' | 'err'; line: string } | null>(null);

	let targets = $derived(['inbox', ...threads.map((t) => t.id).filter((id) => id !== 'inbox')]);

	async function send(event: SubmitEvent) {
		event.preventDefault();
		const words = text.trim();
		if (!words || sending) return;
		sending = true;
		outcome = null;
		try {
			const queued = await postLetter({ thread, text: words, body: body ?? undefined });
			text = '';
			outcome = {
				tone: 'ok',
				line: `queued for ${thread}${queued.event_id ? ` · ${queued.event_id}` : ''}`
			};
		} catch (err) {
			outcome = {
				tone: 'err',
				line: err instanceof LoomAuthError ? 'signed out: sign in to send' : 'not queued, try again'
			};
		} finally {
			sending = false;
		}
	}
</script>

<form class="space-y-2" onsubmit={send} data-testid="composer">
	<textarea
		bind:value={text}
		rows="3"
		placeholder={`a letter to ${thread}`}
		aria-label="letter"
		class="block w-full resize-y border border-stone-700 bg-stone-950/60 p-2.5 text-sm text-stone-100 placeholder:text-ink-mute focus:border-amber-600/70 focus:outline-none"
		onkeydown={(e) => {
			if (e.key === 'Enter' && (e.metaKey || e.ctrlKey))
				(e.currentTarget.form as HTMLFormElement).requestSubmit();
		}}></textarea>
	<div class="flex flex-wrap items-center gap-2 font-mono text-[11px]">
		{#if fixed}
			<span class="text-ink-quiet">to <span class="text-stone-200">{thread}</span></span>
		{:else}
			<label class="flex items-center gap-1.5 text-ink-quiet">
				to
				<select
					bind:value={thread}
					class="border border-stone-700 bg-stone-950 px-1.5 py-1 text-stone-200 focus:border-amber-600/70 focus:outline-none"
					data-testid="composer-thread"
				>
					{#each targets as id (id)}
						<option value={id}>{id}</option>
					{/each}
				</select>
			</label>
		{/if}
		<span class="text-ink-quiet" data-testid="composer-body">
			as <span class="text-stone-200">{body ?? 'the default body'}</span>
		</span>
		<button
			type="submit"
			disabled={sending || !text.trim()}
			class="ml-auto border border-amber-600/70 bg-amber-950/40 px-3 py-1.5 tracking-wide text-amber-100 uppercase transition-colors hover:bg-amber-900/50 disabled:border-stone-700 disabled:bg-transparent disabled:text-ink-mute"
		>
			{sending ? 'sending…' : 'send'}
		</button>
	</div>
	{#if outcome}
		<p
			class={`font-mono text-[11px] ${outcome.tone === 'ok' ? 'text-sky-300/90' : 'text-orange-300'}`}
			role="status"
		>
			{outcome.line}
		</p>
	{/if}
</form>
