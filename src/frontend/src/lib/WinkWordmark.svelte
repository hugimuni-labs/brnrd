<script lang="ts">
	import { onMount } from 'svelte';
	import { pitchAccent } from './statusPalette';

	interface Props {
		/** Resting wordmark; also the aria-label — the wink is presentational. */
		text?: string;
		class?: string;
		/**
		 * Wire-supplied mood frames (#566): `daemon_mood.frames` when the board
		 * is at rest, or the single resolved glyph of the newest live run's
		 * mood. Non-empty ⇒ these play in place of the built-in wink set, on the
		 * same choreography. Null/empty ⇒ the wink, unchanged — this component
		 * owns no emote table and never substitutes a face of its own, so an
		 * unknown or absent mood simply leaves the wordmark as it was.
		 */
		frames?: string[] | null;
		/**
		 * `mood_pitch` ∈ [0,1], the gut→crown body axis, tinting the glyph's
		 * accent hue via `statusPalette.pitchAccent`. Null ⇒ no tint at all;
		 * the mark keeps whatever colour it inherits.
		 */
		pitch?: number | null;
	}

	let { text = 'brnrd', class: klass = '', frames = null, pitch = null }: Props = $props();

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
	// Wire frames arrive without durations (`daemon_mood.frames` is a bare
	// list), so they get one uniform hold — long enough to read a five-glyph
	// face, short enough that a multi-frame breath still completes inside the
	// wink's own window.
	const WIRE_FRAME_MS = 320;

	// The cycle this wordmark plays: the mood's frames when the wire has any,
	// otherwise the built-in wink. Either way the machinery below is the same
	// one — frame index, per-frame hold, settle back to the resting mark.
	let cycle = $derived<Array<[string, number]>>(
		frames && frames.length > 0
			? frames.map((glyphs) => [glyphs, WIRE_FRAME_MS] as [string, number])
			: FRAMES
	);
	let accent = $derived(pitchAccent(pitch));

	// null = at rest (the plain wordmark); otherwise the cycle frame index.
	// The `?? text` guard matters: the mood can change mid-cycle, and a
	// shorter new cycle would otherwise index past its own end.
	let frame = $state<number | null>(null);
	let shown = $derived(frame === null ? text : (cycle[frame]?.[0] ?? text));

	onMount(() => {
		// Reduced-motion readers get the resting mark, permanently.
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		let step: ReturnType<typeof setTimeout> | undefined;
		const wink = () => {
			let i = 0;
			// Read through `cycle` at each step rather than closing over it, so a
			// mood arriving between beats is worn on the next one.
			const advance = () => {
				if (i < cycle.length) {
					frame = i;
					const hold = cycle[i][1];
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

<span class={klass} aria-label={text} style={accent ? `color: ${accent}` : undefined}>{shown}</span>
