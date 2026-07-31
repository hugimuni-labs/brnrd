<script lang="ts">
	import { onMount } from 'svelte';

	// The landing's hero mock (kb/design-brand-visual-language.md's "receipts,
	// not screenshots" instinct, applied to the one thing every competitor's
	// landing page fakes with a GUI recording): a short, *real-shaped*
	// exchange — not lorem, not a stock chat widget. Every line here is a
	// product shape that exists today: a plain-language task, a mid-run
	// progress line, and a close that is the same receipt vocabulary
	// `RunLedgerReceipt.svelte` renders from live data (🔨 commit, 🔀 PR,
	// a green gate line). The one thing a competitor's DOM mock never shows —
	// named directly in the genre research — is continuity: this resident
	// remembers the previous exchange before it does anything else.
	interface Bubble {
		from: 'you' | 'resident';
		kind: 'text' | 'progress' | 'receipt';
		text?: string;
	}

	const BUBBLES: Bubble[] = [
		{
			from: 'you',
			kind: 'text',
			text: 'same repo as tuesday — the login redirect loop is back on staging'
		},
		{
			from: 'resident',
			kind: 'text',
			// The memory beat: continuity is the differentiator, so it is the
			// resident's *first* line, not a footnote.
			text: "remembered — last time it was the cookie's SameSite flag, not the redirect. checking that first."
		},
		{
			from: 'resident',
			kind: 'progress',
			text: 'gate suite running — 4/5 green, patching the cookie flag'
		},
		{ from: 'resident', kind: 'receipt' },
		{ from: 'you', kind: 'text', text: '🙌 merging — nice catch' }
	];

	// Reveal one bubble at a time so the mock reads as a conversation
	// happening, not a screenshot. `prefers-reduced-motion` gets the whole
	// exchange at once — same contract as `.ignite` and `typeReveal`
	// elsewhere on this dashboard (transitions.ts).
	let visibleCount = $state(0);

	onMount(() => {
		const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
		if (mq?.matches) {
			visibleCount = BUBBLES.length;
			return;
		}
		let cancelled = false;
		const timers: ReturnType<typeof setTimeout>[] = [];
		function reveal(i: number) {
			if (cancelled) return;
			visibleCount = i + 1;
			if (i + 1 < BUBBLES.length) {
				timers.push(setTimeout(() => reveal(i + 1), 850));
			}
		}
		timers.push(setTimeout(() => reveal(0), 350));
		return () => {
			cancelled = true;
			for (const t of timers) clearTimeout(t);
		};
	});
</script>

<div class="panel p-4" aria-label="an example exchange with the resident">
	<p class="eyebrow">an actual exchange, not a screenshot</p>
	<div class="mt-3 flex flex-col gap-2">
		{#each BUBBLES as bubble, i (i)}
			{#if i < visibleCount}
				<div class="ignite flex {bubble.from === 'you' ? 'justify-end' : 'justify-start'}">
					<div class="max-w-[85%] sm:max-w-[75%]">
						<p
							class="mb-0.5 font-mono text-[10px] tracking-wide uppercase {bubble.from === 'you'
								? 'text-right text-amber-200/70'
								: 'text-ink-quiet'}"
						>
							{bubble.from === 'you' ? 'you · telegram' : 'resident'}
						</p>
						{#if bubble.kind === 'receipt'}
							<div
								class="subpanel border-l-2 border-l-emerald-700/60 px-3 py-2 text-xs leading-relaxed"
							>
								<div class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-stone-300">
									<span title="commit">🔨 a3f9c1e · SameSite=Lax on session cookie</span>
									<span title="pull request">🔀 PR #142 opened</span>
								</div>
								<p class="mt-1.5 font-mono text-emerald-400">✓ gates green — safe to merge</p>
							</div>
						{:else}
							<div
								class="subpanel px-3 py-2 text-xs leading-relaxed {bubble.from === 'you'
									? 'border-r-2 border-r-amber-700/50'
									: ''} {bubble.kind === 'progress' ? 'text-stone-400 italic' : 'text-stone-200'}"
							>
								{bubble.text}
							</div>
						{/if}
					</div>
				</div>
			{/if}
		{/each}
	</div>
	<!-- The cherry, not the lead: the hosted convenience layer shows up as one
	     small annotation on the exchange it just carried, never as the
	     reason the exchange happened. The reply itself runs the same way
	     with or without it. -->
	<p class="mt-3 border-t border-stone-800/70 pt-2 font-mono text-[10px] text-ink-mute">
		sent from a phone over Telegram · the resident ran on the sender's own machine
	</p>
</div>
