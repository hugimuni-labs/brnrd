<script lang="ts">
	// The wishes: what he asked, what brnrd assumed, what it resulted in, and
	// whether he agrees. Each row is his own words; the thread is bookkeeping
	// and stays tiny. A verdict he owes is the one warm thing here (amber,
	// like 'yours' on a thread card), with the try-it link right above the
	// buttons, because trying it is how he checks. Agree is one tap; Correct
	// opens a box for his words. Both are letters on the wish's thread, so a
	// 202 means queued for the relay, not read, and the row says so until a
	// snapshot carries his answer back; even then an agree is only sent until
	// brnrd records the verdict. Closed wishes fold behind 'N done'.
	import {
		LoomAuthError,
		ago,
		askOwed,
		postLetter,
		refHref,
		sortWishes,
		tryHref,
		verdictOwed,
		wishOpen,
		wishResults,
		type LoomWish,
		type WishVerdict
	} from './loomSnapshot.ts';

	let { wishes, now }: { wishes: LoomWish[]; now: number } = $props();

	const owes = (w: LoomWish) => verdictOwed(w) || askOwed(w);

	let sorted = $derived(sortWishes(wishes));
	// What waits on him leads, ahead of the newest: the first screen answers
	// 'what do I owe' before 'what moved'. Otherwise sortWishes' order stands.
	let open = $derived(sorted.filter(wishOpen).sort((a, b) => Number(owes(b)) - Number(owes(a))));
	let done = $derived(sorted.filter((w) => !wishOpen(w)));
	let owed = $derived(open.filter(owes).length);
	let showDone = $state(false);

	// Per wish: the box Correct opened, a tap in flight, and what the POST said.
	let drafts = $state<Record<string, string>>({});
	let sending = $state<Record<string, boolean>>({});
	let sent = $state<Record<string, { verdict: WishVerdict; at: string }>>({});
	let failed = $state<Record<string, string>>({});

	async function answer(wish: LoomWish, verdict: WishVerdict) {
		const text = verdict === 'agree' ? 'agree' : (drafts[wish.id] ?? '').trim();
		if (!text || sending[wish.id]) return;
		sending[wish.id] = true;
		delete failed[wish.id];
		try {
			await postLetter({ thread: wish.thread ?? 'inbox', text, wish: wish.id, verdict });
			sent[wish.id] = { verdict, at: new Date().toISOString() };
			delete drafts[wish.id];
		} catch (err) {
			failed[wish.id] =
				err instanceof LoomAuthError ? 'signed out: sign in to send' : 'not queued, try again';
		} finally {
			sending[wish.id] = false;
		}
	}

	const who = (from: string | undefined) =>
		!from || from.startsWith('person:') ? 'you' : from.startsWith('s-') ? 'brnrd' : from;

	/** His answer, in words that never read as done: an agree is a verdict
	 * only once brnrd records it (his relay login must be enrolled), and a
	 * tap this page just sent is only queued for the relay. */
	function answered(wish: LoomWish, queued?: { verdict: WishVerdict; at: string }): string {
		const agree = (wish.answer?.verdict ?? queued?.verdict) === 'agree';
		const when = ago(wish.answer?.at ?? queued?.at, now);
		const state = !wish.answer
			? 'queued for the relay, not delivered yet'
			: agree
				? 'sent, not recorded yet'
				: 'sent to brnrd';
		const said = agree
			? 'you said agree'
			: wish.from === 'resident'
				? 'you answered'
				: 'you asked for a correction';
		return [said, when, state].filter(Boolean).join(' · ');
	}
</script>

{#snippet beat(label: string)}
	<span class="pt-0.5 font-mono text-[10px] tracking-wide text-ink-mute uppercase">{label}</span>
{/snippet}

{#snippet row(wish: LoomWish)}
	{@const closed = !wishOpen(wish)}
	{@const mine = owes(wish)}
	{@const asks = wish.from === 'resident'}
	{@const results = wishResults(wish)}
	{@const result = results[0]}
	{@const tryIt = result ? tryHref(result) : null}
	{@const refIt = result && !tryIt ? refHref(result) : null}
	{@const queued = !wish.answer && !closed ? sent[wish.id] : undefined}
	<article
		class={`subpanel border-l-2 p-3 ${
			mine
				? 'border-l-amber-400 bg-amber-950/25 shadow-[inset_3px_0_12px_-6px_rgba(251,191,36,0.6)]'
				: closed
					? 'border-l-stone-800 opacity-70'
					: 'border-l-sky-400/50'
		}`}
		data-testid="wish"
		data-wish={wish.id}
		data-owed={mine ? 'true' : 'false'}
	>
		<p class="mb-2 flex flex-wrap items-baseline gap-x-2 font-mono text-[10px] tracking-wide">
			{#if mine}
				<span class="text-amber-300 uppercase">{asks ? 'brnrd asks you' : 'your verdict'}</span>
			{:else if asks}
				<span class="text-ink-quiet uppercase">brnrd asks you</span>
			{/if}
			{#if wish.state}<span class={closed ? 'text-ink-mute' : 'text-sky-300/80'}>{wish.state}</span
				>{/if}
			{#if wish.thread}<span class="text-ink-mute">{wish.thread}</span>{/if}
			{#if ago(wish.at, now)}<span class="ml-auto text-ink-mute">{ago(wish.at, now)}</span>{/if}
		</p>

		<div class="grid grid-cols-[3.75rem_minmax(0,1fr)] gap-x-2 gap-y-2.5">
			{@render beat('asked')}
			<div class="min-w-0" data-beat="asked">
				{#if wish.origin === 'derived'}
					<p class={`text-[15px] leading-snug ${closed ? 'text-stone-400' : 'text-stone-200'}`}>
						{wish.line ?? ''}
					</p>
					<p class="mt-0.5 font-mono text-[10px] text-ink-quiet" data-testid="derived">
						brnrd’s reading, not your words
					</p>
				{:else if (wish.says ?? []).length}
					{#each wish.says ?? [] as say (say.id)}
						<p
							class={`line-clamp-3 text-[15px] leading-snug break-words ${
								mine ? 'font-semibold text-amber-50' : closed ? 'text-stone-400' : 'text-stone-100'
							}`}
						>
							“{say.head ?? ''}”
						</p>
						<p class="mt-0.5 mb-1 font-mono text-[10px] text-ink-mute">
							{[who(say.from), say.at !== wish.at && ago(say.at, now)].filter(Boolean).join(' · ')}
						</p>
					{/each}
				{:else}
					<p class={`text-[15px] leading-snug ${closed ? 'text-stone-400' : 'text-stone-200'}`}>
						{wish.line ?? ''}
					</p>
				{/if}
			</div>

			{@render beat('assumed')}
			<p
				class={`min-w-0 text-sm leading-snug break-words ${wish.reading ? 'text-stone-300' : 'font-mono text-[11px] text-ink-mute'}`}
				data-beat="assumed"
			>
				{wish.reading ?? 'not read yet'}
			</p>

			{@render beat('resulted')}
			<div class="min-w-0" data-beat="resulted">
				{#if result}
					<p class="text-sm leading-snug break-words text-stone-300">
						{result.line || result.kind || result.ref || 'a result'}
						<span
							class={`ml-1 font-mono text-[10px] ${result.delivered ? 'text-sky-300/90' : 'text-ink-mute'}`}
							>{result.delivered ? 'delivered' : 'not delivered yet'}</span
						>
					</p>
					<!-- eslint-disable svelte/no-navigation-without-resolve -- external results -->
					{#if tryIt}
						<a
							href={tryIt}
							target="_blank"
							rel="noopener noreferrer"
							class="mt-1.5 inline-flex min-h-11 max-w-full items-center gap-1.5 border border-sky-400/60 bg-sky-950/40 px-3 font-mono text-[12px] text-sky-100 hover:bg-sky-900/50"
							data-testid="try-it"
						>
							<span class="shrink-0 whitespace-nowrap">try it ↗</span>
							<span class="truncate text-sky-300/80">{tryIt.replace(/^https?:\/\//, '')}</span>
						</a>
					{:else if refIt}
						<a
							href={refIt}
							target="_blank"
							rel="noopener noreferrer"
							class="mt-1 inline-flex min-h-11 max-w-full items-center font-mono text-[11px] text-sky-300/90 hover:text-sky-200"
							data-testid="result-ref"
						>
							<span class="truncate">↗ {result.kind === 'pr' ? 'the PR' : refIt}</span>
						</a>
					{:else if result.ref}
						<p class="mt-0.5 truncate font-mono text-[10px] text-ink-quiet">{result.ref}</p>
					{/if}
					<!-- eslint-enable svelte/no-navigation-without-resolve -->
					{#if results.length > 1}
						<p class="mt-0.5 font-mono text-[10px] text-ink-mute">+{results.length - 1} earlier</p>
					{/if}
				{:else}
					<p class="font-mono text-[11px] text-ink-mute">nothing yet</p>
				{/if}
			</div>

			{@render beat('you')}
			<div class="min-w-0" data-beat="you">
				{#if closed}
					<p class="font-mono text-[11px] text-ink-quiet">
						<span class="text-stone-300"
							>{wish.verdict?.as === 'dropped' || wish.state === 'dropped'
								? 'dropped'
								: 'agreed'}</span
						>{#if ago(wish.verdict?.at, now)}
							· {ago(wish.verdict?.at, now)}{/if}
					</p>
				{:else if wish.answer || queued}
					<p
						class="font-mono text-[11px] text-sky-300/90"
						role={wish.answer ? undefined : 'status'}
						data-testid="answered"
					>
						{answered(wish, queued)}
					</p>
					{#if wish.answer?.head && wish.answer.verdict !== 'agree'}
						<p class="mt-0.5 line-clamp-2 text-sm text-stone-400">“{wish.answer.head}”</p>
					{/if}
				{:else}
					{#if drafts[wish.id] !== undefined}
						<textarea
							bind:value={drafts[wish.id]}
							rows="3"
							placeholder={asks ? 'your answer' : 'what should it be instead?'}
							aria-label={asks ? 'your answer' : 'your correction'}
							class="mb-2 block w-full resize-y border border-stone-700 bg-stone-950/60 p-2.5 text-sm text-stone-100 placeholder:text-ink-mute focus:border-amber-600/70 focus:outline-none"
						></textarea>
					{/if}
					<div class="flex flex-wrap gap-2 font-mono text-[11px] tracking-wide uppercase">
						{#if drafts[wish.id] !== undefined}
							<button
								type="button"
								disabled={sending[wish.id] || !drafts[wish.id].trim()}
								class="min-h-11 border border-amber-600/70 bg-amber-950/40 px-4 text-amber-100 transition-colors hover:bg-amber-900/50 disabled:border-stone-700 disabled:bg-transparent disabled:text-ink-mute"
								onclick={() => answer(wish, 'correct')}
							>
								{sending[wish.id] ? 'sending…' : asks ? 'send answer' : 'send correction'}
							</button>
							<button
								type="button"
								class="min-h-11 px-3 text-ink-quiet hover:text-stone-200"
								onclick={() => delete drafts[wish.id]}
							>
								cancel
							</button>
						{:else}
							{#if wish.state === 'delivered'}
								<button
									type="button"
									disabled={sending[wish.id]}
									class="min-h-11 min-w-24 border border-amber-500/80 bg-amber-900/40 px-4 text-amber-50 transition-colors hover:bg-amber-800/50 disabled:text-ink-mute"
									data-testid="agree"
									onclick={() => answer(wish, 'agree')}
								>
									{sending[wish.id] ? 'sending…' : 'agree'}
								</button>
							{/if}
							<button
								type="button"
								class="min-h-11 min-w-24 border border-stone-700 px-4 text-stone-200 transition-colors hover:border-stone-500"
								data-testid="correct"
								onclick={() => (drafts[wish.id] = '')}
							>
								{asks ? 'answer' : 'correct'}
							</button>
						{/if}
					</div>
					{#if failed[wish.id]}
						<p class="mt-1.5 font-mono text-[11px] text-orange-300" role="status">
							{failed[wish.id]}
						</p>
					{/if}
				{/if}
			</div>
		</div>
	</article>
{/snippet}

<section class="mb-4" aria-labelledby="wishes-heading" data-testid="wishes">
	<div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
		<h2 id="wishes-heading" class="eyebrow">wishes</h2>
		{#if wishes.length}
			<p class="font-mono text-[10px] tracking-wide" data-testid="wishes-owed">
				{#if owed}
					<span class="text-amber-300">{owed} wait{owed === 1 ? 's' : ''} on you</span>
				{:else}
					<span class="text-ink-mute">nothing waits on you</span>
				{/if}
			</p>
		{/if}
	</div>
	<div class="grid gap-2">
		{#each open as wish (wish.id)}
			{@render row(wish)}
		{:else}
			<p class="subpanel p-3 font-mono text-[11px] text-ink-mute" data-testid="wishes-empty">
				{wishes.length
					? 'nothing open: every wish has a verdict'
					: 'no wishes yet: what you ask brnrd for shows up here once it stamps it'}
			</p>
		{/each}
		{#if done.length}
			<button
				type="button"
				class="min-h-11 text-left font-mono text-[11px] text-ink-quiet hover:text-amber-200"
				aria-expanded={showDone}
				data-testid="wishes-done"
				onclick={() => (showDone = !showDone)}
			>
				{showDone ? 'hide' : 'show'}
				{done.length} done
			</button>
			{#if showDone}
				{#each done as wish (wish.id)}
					{@render row(wish)}
				{/each}
			{/if}
		{/if}
	</div>
</section>
