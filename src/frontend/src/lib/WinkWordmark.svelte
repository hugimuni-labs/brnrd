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
	// shells — which is the product made visible. Every frame is authored as
	// five codepoints, meant to hold the mark's width steady mid-wink — and
	// measured (2026-08-05, both mid-mono directly and independently by the
	// maintainer against a live screenshot): that is a true fact about the
	// *string* (`brr/emotes.py`'s own rule, "so the mark never jitters" — 113
	// emotes, 405 frames, all exactly 5 codepoints, no exceptions) and a false
	// one about the *rendering*. `layout.css` never actually sets `--font-mono`,
	// so `font-mono` runs on the browser's raw `ui-monospace, 'SF Mono', …`
	// fallback list, and at least nine of the glyphs this component is asked to
	// show (`Я` in the built-in `bRnЯd`; `· ¬ ° ᵕ ‿ ᴗ ˋ ˊ` among the wire
	// frames) are plausibly missing from any one member of that stack — a
	// missing glyph free-floats to whatever font a browser substitutes for it,
	// at whatever width that font gives it. On mobile the observed failure was
	// worse than jitter: a wider substituted glyph pushed the un-wrapped
	// content past the header's width, and the browser's own line-breaker
	// found a legal break at the ASCII `-` inside a frame (`b^n-d` → `b^n-` /
	// `d` on two lines), shoving the header down. `nowrap` below stops that
	// outright; the stacked box (see its own comment) stops the reflow between
	// frames of genuinely different rendered width, which counting codepoints
	// cannot detect and this SSR-only test harness cannot measure either (no
	// real layout runs here — the tests below assert the stacked *markup*,
	// not pixels). The honest fix is a font stack with guaranteed glyph
	// coverage; `--font-mono` staying unset is a separate, pre-existing gap,
	// left alone here.
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
	// A shorter new cycle arriving mid-wink is handled below (each stacked
	// frame renders only while `frame` still points at it; nothing is
	// selected once `frame` runs past the new cycle's end).
	let frame = $state<number | null>(null);

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

<!--
	Two floors, not one (2026-08-05, the maintainer's own screenshot plus the
	measurement above). `whitespace-nowrap` stops the worse of the two observed
	failures outright: content that used to be free to wrap, wrapped mid-frame
	at the ASCII `-` in `b^n-d`, shoving the header down a line. That alone
	still leaves the *box* free to change size frame to frame if the rendered
	widths genuinely differ, so every candidate frame (the resting text plus
	the whole cycle) is stacked in one grid cell instead of swapped in as a
	single text node. A `ch`-based reservation was the other option on the
	table and was rejected: `ch` is itself a monospace-metric assumption, and
	the whole failure is that assumption being false for some of this
	component's own glyphs. Stacking sidesteps the metric entirely — the grid
	measures every frame the browser actually rendered, real substituted font
	and all, and sizes the cell to the widest one. That also covers a future
	wire frame (a wider emote, #566) at no extra cost: there is no palette this
	component has to know about or keep in sync with, only whatever `cycle` it
	is handed right now.
-->
<span
	class="relative inline-grid whitespace-nowrap {klass}"
	aria-label={text}
	style={accent ? `color: ${accent}` : undefined}
>
	<span
		class="[grid-area:1/1]"
		aria-hidden="true"
		style={frame === null ? undefined : 'visibility: hidden'}>{text}</span
	>
	{#each cycle as [glyphs], i (i)}
		<span
			class="[grid-area:1/1]"
			aria-hidden="true"
			style={frame === i ? undefined : 'visibility: hidden'}>{glyphs}</span
		>
	{/each}
</span>
