<script lang="ts">
	// The resident's mood, worn beside a run's status word (#566) — and, as of
	// the polish round (evt-…-purq/v7od/97z6/ubrl), *on* the run node as a face
	// with its own stage. One component for every rendering of a run so the
	// surfaces cannot disagree about what a mood looks like, and the house rule
	// lives in exactly one place:
	//
	//   an unknown or absent mood renders as NOTHING or the bare handle name,
	//   never a guessed or default face (`brr.emotes`' own docstring).
	//
	// `moodFace` in `liveRuns.ts` is what enforces it; this file only draws
	// what it returns.
	//
	// ── The choreography ──────────────────────────────────────────────────
	//
	// A mood rests expressive and flashes neutral — the feeling is the message,
	// the blink is the punctuation (maintainer, evt-…-cwcw). So the face holds
	// its authored `rest` for a long calm beat, then blinks; and the *blink* is
	// where the product shows through: a one-frame CRT blip of scramble static
	// (the `typeReveal` glyph alphabet, deterministic via `glitchNoise` — never
	// `Math.random`, so two polls draw the same frame), then the `brnrd`
	// wordmark writing itself over the face, then the emote's authored wink
	// frames, one closing blip, and back to rest. That folds evt-…-97z6's
	// "write brnrd, switch to the emote, wink, back" into the cwcw contract
	// instead of inverting it: the wordmark is the punctuation's body, not the
	// resting state. Blinks come in pairs — calm ~5s, blink, ~1s, blink again
	// (evt-…-purq: one wink per 5s read as too little) — with alternates
	// (`sequences[1..]`, evt-…-csi8) rotating one per blink.
	//
	// The resting face is `rest` — per-emote, wire-resolved, *not* `frames[0]`:
	// `frames[0]` is the shared animation base (61 of 98 faces identical
	// there), which is the starvation #699 fixed. Where an emote has no
	// distinct `rest` yet the wire falls back and the face is still ambiguous
	// at rest — a palette gap, tracked separately.
	//
	// Each chip's phase is offset by a hash of its seed so a grid of live runs
	// breathes independently instead of pulsing in unison.
	import { onMount } from 'svelte';
	import type { MoodFace } from './liveRuns';
	import { TYPE_REVEAL_GLYPHS, glitchNoise } from './transitions.ts';

	interface Props {
		face?: MoodFace | null;
		/** Per-chip animation phase source (a run id). Same seed ⇒ same phase,
		 *  so a re-render doesn't make the face jump. */
		seed?: string;
		/** `inline` rides beside the status word at 9px, glyph + handle name.
		 *  `stage` is the run node's centered face: larger, glyph only — the
		 *  handle lives in the title tooltip (evt-…-purq: the word doubled the
		 *  glyph once the glyph became legible). */
		variant?: 'inline' | 'stage';
		class?: string;
	}

	let { face = null, seed = '', variant = 'inline', class: klass = '' }: Props = $props();

	/** Calm between blink pairs; the short beat inside a pair; how fast the
	 *  blink's own frames run; and the punctuation frames' timing. */
	const CALM_MS = 5000;
	const REBLINK_MS = 1000;
	const FLICKER_FRAME_MS = 190;
	const BLIP_MS = 90;
	const WORDMARK_MS = 160;
	const WORDMARK = 'brnrd';

	/** Cheap deterministic phase spread. Not security, not a distribution —
	 *  just enough that two rows mounted in the same tick don't share one. */
	function phaseOf(key: string): number {
		let h = 0;
		for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
		return Math.abs(h) % 2400;
	}

	let sequences = $derived(face?.sequences ?? null);
	/** The face at rest. Wire-supplied per emote; falls back to the first
	 *  frame of the primary cycle, then to the lone glyph a pre-upgrade
	 *  daemon sends. Never invented here. */
	let restGlyph = $derived(face?.rest ?? sequences?.[0]?.[0] ?? face?.glyph ?? null);

	/** One frame of CRT static the width of the face, drawn — not rolled —
	 *  from (cell, salt), so it is reproducible across re-renders. */
	function staticOf(width: number, salt: number): string {
		let s = '';
		for (let i = 0; i < width; i++) {
			s += TYPE_REVEAL_GLYPHS[Math.floor(glitchNoise(i, salt) * TYPE_REVEAL_GLYPHS.length)];
		}
		return s;
	}

	interface Beat {
		glyph: string;
		ms: number;
	}

	/** One blink, assembled: static blip → wordmark → authored wink frames →
	 *  closing blip. The scanline switch between animated states
	 *  (evt-…-ubrl) is the two blips; the neutral flash (cwcw / 97z6) is the
	 *  wordmark passing through. */
	function blinkOf(cycle: number): Beat[] {
		const cycles = sequences;
		if (!cycles || cycles.length === 0) return [];
		const seq = cycles[cycle % cycles.length];
		const width = Array.from(restGlyph ?? seq[0] ?? WORDMARK).length;
		return [
			{ glyph: staticOf(width, cycle * 7 + 1), ms: BLIP_MS },
			{ glyph: WORDMARK, ms: WORDMARK_MS },
			...seq.map((f) => ({ glyph: f, ms: FLICKER_FRAME_MS })),
			{ glyph: staticOf(width, cycle * 7 + 3), ms: BLIP_MS }
		];
	}

	let playing = $state<string | null>(null);
	let reduced = $state(false);

	onMount(() => {
		// Respect the OS switch: a mood is expressive, never urgent. Held
		// still, the resting face still carries the handle (inline text or
		// stage tooltip), which is the whole fact — the motion was only ever
		// the delivery.
		const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
		reduced = !!mq?.matches;
		const onChange = (e: MediaQueryListEvent) => (reduced = e.matches);
		mq?.addEventListener?.('change', onChange);

		let timer: ReturnType<typeof setTimeout> | undefined;
		let cancelled = false;
		let cycle = 0;

		/** Walk one blink beat by beat, then go still and queue the next.
		 *  Blinks pair up: the odd cycle follows after REBLINK_MS, the even
		 *  one waits out the calm. */
		function step(beats: Beat[], beatIndex: number) {
			if (cancelled) return;
			if (reduced || beats.length === 0) {
				playing = null;
				timer = setTimeout(() => step(blinkOf(cycle), 0), CALM_MS);
				return;
			}
			if (beatIndex >= beats.length) {
				playing = null; // back to rest
				cycle += 1;
				const pause = cycle % 2 === 1 ? REBLINK_MS : CALM_MS;
				timer = setTimeout(() => step(blinkOf(cycle), 0), pause);
				return;
			}
			playing = beats[beatIndex].glyph;
			timer = setTimeout(() => step(beats, beatIndex + 1), beats[beatIndex].ms);
		}

		// Start calm, offset per chip, so the grid never blinks in chorus.
		timer = setTimeout(() => step(blinkOf(0), 0), CALM_MS + phaseOf(seed || face?.name || ''));
		return () => {
			cancelled = true;
			if (timer) clearTimeout(timer);
			mq?.removeEventListener?.('change', onChange);
		};
	});

	// The animating frame when one is playing, else the resting face, else
	// nothing. Inline, the name always rides along: an unknown handle has no
	// face at all, and the bare name is the honest render of that.
	let glyph = $derived(playing ?? restGlyph);
</script>

{#if face}
	{#if variant === 'stage'}
		{#if glyph}
			<span
				class="font-mono text-sm tracking-[0.2em] whitespace-pre text-amber-200/90 {klass}"
				title="mood: {face.name}"
			>
				{glyph}
			</span>
		{:else}
			<!-- No face resolved: the bare handle, dim — never a guessed glyph. -->
			<span class="font-mono text-[10px] text-ink-mute {klass}" title="mood">{face.name}</span>
		{/if}
	{:else}
		{@const label = glyph ? `${glyph} ${face.name}` : face.name}
		<span
			class="min-w-0 shrink truncate font-mono text-[9px] whitespace-pre text-ink-mute {klass}"
			title="mood: {face.name}"
		>
			{label}
		</span>
	{/if}
{/if}
