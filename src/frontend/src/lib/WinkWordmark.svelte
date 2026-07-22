<script lang="ts">
	import { onMount } from 'svelte';

	interface Props {
		/** Resting wordmark; also the aria-label — the wink is presentational. */
		text?: string;
		class?: string;
	}

	let { text = 'brnrd', class: klass = '' }: Props = $props();

	// The wink (2026-07-22 ask): every few seconds the wordmark glitches
	// through its other bodies and settles back. Same name, different
	// shells — which is the product made visible. Every frame is five
	// mono glyphs, so the mark never changes width mid-wink.
	//
	// Choreography per the maintainer's steers (evt-y2em, evt-58bk):
	// bRnЯd and the face far apart; the eyes open one at a time
	// (b-n-d → b^n-d → b^n^d); the ^^ face holds a while; the wink itself
	// (b^n<d) is quick; the resting wordmark hangs long between cycles.
	// Each frame carries its own duration.
	const FRAMES: Array<[string, number]> = [
		['bRnЯd', 140],
		['brnrd', 900],
		['b-n-d', 220],
		['b^n-d', 220],
		['b^n^d', 1400],
		['b^n<d', 140],
		['b^n^d', 500]
	];
	const PERIOD_MS = 9000;
	const FIRST_MS = 1800;

	// null = at rest (the plain wordmark); otherwise the wink frame index.
	let frame = $state<number | null>(null);
	let shown = $derived(frame === null ? text : FRAMES[frame][0]);

	onMount(() => {
		// Reduced-motion readers get the resting mark, permanently.
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		let step: ReturnType<typeof setTimeout> | undefined;
		const wink = () => {
			let i = 0;
			const advance = () => {
				if (i < FRAMES.length) {
					frame = i;
					const hold = FRAMES[i][1];
					i += 1;
					step = setTimeout(advance, hold);
				} else {
					frame = null;
				}
			};
			advance();
		};
		const first = setTimeout(wink, FIRST_MS);
		const loop = setInterval(wink, PERIOD_MS);
		return () => {
			clearTimeout(first);
			clearInterval(loop);
			if (step) clearTimeout(step);
		};
	});
</script>

<span class={klass} aria-label={text}>{shown}</span>
