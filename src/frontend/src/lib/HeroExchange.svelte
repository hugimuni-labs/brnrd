<script lang="ts">
	import { onMount } from 'svelte';
	import { GITHUB_REPO } from '$lib/publicStats';

	// The landing's hero exchange. It is not a mock, and this file used to lie
	// about that (2026-07-31): the first version rendered an *invented*
	// conversation under the eyebrow "an actual exchange, not a screenshot" —
	// a claim meant to say "live DOM, not an image" that every visitor reads
	// as "this happened". It hadn't. The maintainer felt it as "slightly
	// artificial" within ten minutes of the deploy, and his own brief that
	// afternoon had already asked for the opposite: *"the whole conversation
	// mock should be real"*.
	//
	// So every line below is verbatim from this repository's own Telegram
	// thread on 2026-07-31 at 21:54 CEST — the maintainer's message as he
	// typed it, the resident's reply as it was sent, trimmed at sentence
	// boundaries and never rewritten. Every number resolves: #908, #909 and
	// #903 are real pull requests and issues in the repository this page
	// links to, and `c05699d7` is the commit that merged #908. The invented
	// version used `a3f9c1e`, `PR #142` and "4/5 green" — smaller, rounder
	// and less impressive than the truth they replaced, which is the whole
	// argument for never inventing here again.
	//
	// The one thing no competitor's landing shows (genre research,
	// `research-peer-landing-and-identity-2026-07-31.md`) is continuity: the
	// resident's second line recognises a test and remembers filing the issue
	// for it earlier that same day, unprompted. That beat is the product, so
	// it is not a footnote.
	const REPO_URL = `https://github.com/${GITHUB_REPO}`;

	interface Chip {
		icon: string;
		label: string;
		href: string;
		title: string;
	}

	interface Bubble {
		from: 'you' | 'resident';
		kind: 'text' | 'receipt';
		text?: string;
		/** Milliseconds after the previous bubble. Deliberately uneven: a fixed
		 *  metronome is the tell that a reveal was scripted, and the real gap
		 *  before a receipt is the length of the work. */
		gap: number;
	}

	const BUBBLES: Bubble[] = [
		{
			from: 'you',
			kind: 'text',
			text: 'merged 908, the other 2 have red ci 🤔',
			gap: 350
		},
		{
			from: 'resident',
			kind: 'text',
			text: 'Both reds decoded, neither is the diffs — and 🙌 for #908, the hero ships.',
			gap: 1100
		},
		{
			from: 'resident',
			kind: 'text',
			// The memory beat, verbatim.
			text: '#909 (docs): backend red = test_dashboard_coarse_recheck_only_touches_stale_repos — that’s #903, the flaky thread-race filed this afternoon. A docs-only diff has zero .py changes; it cannot reach that code path. Retriggered.',
			gap: 1900
		},
		{ from: 'resident', kind: 'receipt', gap: 1500 }
	];

	// The receipts are links, not decorations: every chip opens the thing it
	// names. A receipt you cannot click is the same genre of claim as the
	// exchange this file used to invent.
	const CHIPS: Chip[] = [
		{
			icon: '🔨',
			label: 'c05699d7',
			href: `${REPO_URL}/commit/c05699d75f9f27352f06da31c3dad340acb6f60f`,
			title: 'commit'
		},
		{ icon: '🔀', label: '#908 merged', href: `${REPO_URL}/pull/908`, title: 'pull request' },
		{ icon: '🐛', label: '#903 filed', href: `${REPO_URL}/issues/903`, title: 'issue' }
	];

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
			const next = BUBBLES[i + 1];
			if (next) timers.push(setTimeout(() => reveal(i + 1), next.gap));
		}
		timers.push(setTimeout(() => reveal(0), BUBBLES[0].gap));
		return () => {
			cancelled = true;
			for (const t of timers) clearTimeout(t);
		};
	});
</script>

<div class="panel p-4" aria-label="a real exchange with the resident">
	<p class="eyebrow">a real exchange · 31 july 2026 · this repository's own thread</p>
	<div class="mt-3 flex flex-col gap-2">
		{#each BUBBLES as bubble, i (i)}
			{#if i < visibleCount}
				<div class="ignite flex {bubble.from === 'you' ? 'justify-end' : 'justify-start'}">
					<!-- `min-w-0` + `break-words`: real work carries unbroken tokens a
					     copywriter never types — a 52-character test name here — and
					     without these it escapes the panel on every width. -->
					<div class="max-w-[85%] min-w-0 sm:max-w-[75%]">
						<p
							class="mb-0.5 font-mono text-[10px] tracking-wide uppercase {bubble.from === 'you'
								? 'text-right text-amber-200/70'
								: 'text-ink-quiet'}"
						>
							{bubble.from === 'you' ? 'you · telegram' : 'resident'}
						</p>
						{#if bubble.kind === 'receipt'}
							<div
								class="subpanel border-l-2 border-l-amber-700/60 px-3 py-2 text-xs leading-relaxed"
							>
								<div class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-stone-300">
									{#each CHIPS as chip (chip.href)}
										<a
											class="underline decoration-stone-600 underline-offset-2 hover:text-amber-200"
											href={chip.href}
											rel="external"
											title={chip.title}>{chip.icon} {chip.label}</a
										>
									{/each}
								</div>
								<!-- No test count here on purpose. The number that would go in
							     this slot grows every week, so a figure baked into the
							     markup is a slow lie; the two facts below do not move. -->
								<p class="mt-1.5 font-mono text-amber-300">
									✓ gate green — merged, deployed 21:51 CEST
								</p>
							</div>
						{:else}
							<div
								class="subpanel px-3 py-2 text-xs leading-relaxed break-words text-stone-200 {bubble.from ===
								'you'
									? 'border-r-2 border-r-amber-700/50'
									: ''}"
							>
								{bubble.text}
							</div>
						{/if}
					</div>
				</div>
			{/if}
		{/each}
	</div>
	<!-- Two facts the page can back. The provenance line is load-bearing: it
	     is what lets the eyebrow above say "real" without overclaiming, and it
	     names the trim honestly. The cherry is the hosted layer showing up as
	     one annotation on an exchange it merely carried — never as the reason
	     the exchange happened. -->
	<p class="mt-3 border-t border-stone-800/70 pt-2 font-mono text-[10px] text-ink-mute">
		verbatim, trimmed for length, never rewritten · sent from a phone over Telegram · the resident
		ran on the sender's own machine
	</p>
</div>
