<script lang="ts">
	import { onMount } from 'svelte';
	import { GITHUB_REPO } from '$lib/publicStats';

	// A real exchange from this repository's Telegram thread on 2026-07-31.
	// Lines stay verbatim; the long diagnostic is trimmed at sentence boundaries
	// so the proof does not swallow the landing on a phone. The continuity beat
	// remains: the resident recognises #903 from earlier that same afternoon.
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
			text: '#909 (docs): backend red = test_dashboard_coarse_recheck_only_touches_stale_repos — that’s #903, the flaky thread-race filed this afternoon. Retriggered.',
			gap: 1700
		},
		{ from: 'resident', kind: 'receipt', gap: 1200 }
	];

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
	<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
		<p class="eyebrow">a real exchange</p>
		<p class="font-mono text-[9px] tracking-wide text-ink-mute uppercase">you · Telegram ↔ resident</p>
	</div>
	<div class="mt-3 flex flex-col gap-2">
		{#each BUBBLES as bubble, i (i)}
			{#if i < visibleCount}
				<div class="ignite flex {bubble.from === 'you' ? 'justify-end' : 'justify-start'}">
					<div class="max-w-[88%] min-w-0 sm:max-w-[78%]">
						<p
							class="mb-0.5 font-mono text-[9px] tracking-wide uppercase {bubble.from === 'you'
								? 'text-right text-amber-200/70'
								: 'text-ink-quiet'}"
						>
							{bubble.from === 'you' ? 'you' : 'resident'}
						</p>
						{#if bubble.kind === 'receipt'}
							<div class="subpanel border-l-2 border-l-amber-700/60 px-3 py-2 text-xs leading-relaxed">
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
								<p class="mt-1.5 font-mono text-amber-300">✓ gate green — merged, deployed 21:51 CEST</p>
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
	<p class="mt-3 border-t border-stone-800/70 pt-2 font-mono text-[9px] leading-relaxed text-ink-mute">
		verbatim · trimmed for length · 31 July 2026 · this repository's own thread · resident ran on
		the sender's machine
	</p>
</div>
