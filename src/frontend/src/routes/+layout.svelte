<script lang="ts">
	import { onMount } from 'svelte';
	import './layout.css';
	import { markBooted } from '$lib/boot';
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();

	// Boot glitch (kb/design-brand-visual-language.md §3): a real spec,
	// named in enough detail to be checkable, never built until this pass.
	// `_` -> `b_d` -> `br_rd` -> `brnrd` -glitch-> `bRnЯd` — each frame adds
	// one letter-pair symmetrically around the underscore cursor (the
	// mirror axis `b`/`d` and `R`/`Я` already share), then the final frame
	// gets a brief chromatic flicker before the overlay lifts. Skipped
	// entirely under prefers-reduced-motion rather than just shortened —
	// the letters-converging motion *is* the content here, there's no
	// reduced-but-still-meaningful version of it.
	const FRAMES = ['_', 'b_d', 'br_rd', 'brnrd', 'bRnЯd'];
	const FRAME_MS = 190;

	let booting = $state(false);
	let frameIndex = $state(0);
	let flicker = $state(false);

	onMount(() => {
		const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		// No curtain under reduced motion, so nothing is waiting on one.
		if (reduced) {
			markBooted();
			return;
		}
		booting = true;
		let i = 0;
		const step = () => {
			i += 1;
			if (i >= FRAMES.length) {
				flicker = true;
				setTimeout(() => {
					booting = false;
					// The text reveal is held until here: playing it behind an opaque
					// overlay is the same as not playing it (see `$lib/boot`).
					markBooted();
				}, 260);
				return;
			}
			frameIndex = i;
			if (i === FRAMES.length - 1) flicker = true;
			setTimeout(step, FRAME_MS);
		};
		setTimeout(step, FRAME_MS);
	});
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>

{#if booting}
	<div
		class="fixed inset-0 z-100 flex items-center justify-center bg-[#0c0906] transition-opacity duration-300"
	>
		<span class={`boot-glitch ${flicker ? 'is-flicker' : ''}`}>{FRAMES[frameIndex]}</span>
	</div>
{/if}
{@render children()}
