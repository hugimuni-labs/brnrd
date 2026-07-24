<script lang="ts">
	// The resident's mood, worn quietly beside a run's status word (#566).
	// One component for both renderings of a run — the LiveRuns grid card and
	// the selected-node panel — so the two surfaces cannot disagree about what
	// a mood looks like, and the house rule lives in exactly one place:
	//
	//   an unknown or absent mood renders as NOTHING or the bare handle name,
	//   never a guessed or default face (`brr.emotes`' own docstring).
	//
	// `moodFace` in `liveRuns.ts` is what enforces it; this file only draws
	// what it returns. Deliberately smaller and dimmer than the status word:
	// the mood is colour on a fact, not the fact.
	//
	// ── The choreography, and why it isn't the wink's ─────────────────────
	//
	// The wordmark's wink rests neutral and glitches through its other bodies:
	// that is the *product* winking, and the resting mark is the brand. A mood
	// is a different object — it belongs to a run, not to the company — so it
	// gets the opposite emphasis (maintainer, evt-…-cwcw): calm for a long
	// beat on its resting face, then a quick flicker through the animation,
	// then calm again. Roughly 5s still, ~1s moving.
	//
	// The resting face is `rest` — a per-emote frame the daemon resolves and
	// puts on the wire, *not* `frames[0]`. That distinction is the whole bug
	// this component was starved by: `frames[0]` is shared by design (every
	// name-weave face rests on the plain wordmark, every cheek face on neutral
	// eyes), so across the 98 situational emotes there are 15 distinct resting
	// frames and 61 of them are the identical `b·_·d`. A chip resting on
	// `frames[0]` renders smug, weary and triumphant the same way for 5 of
	// every 6 seconds. Where an emote has no distinct `rest` authored yet, the
	// wire falls back to `frames[0]` and that face is still indistinguishable
	// at rest — a palette gap, tracked separately, not something this file can
	// fix by picking a frame of its own.
	//
	// Alternates (`sequences[1..]`, evt-…-csi8) rotate one per flicker: one
	// face with two breaths reads alive, one face with one breath reads like a
	// spinner. Each chip's phase is offset by a hash of its seed so a grid of
	// live runs breathes independently instead of pulsing in unison — the
	// failure mode of N identical timers started in the same tick.
	import { onMount } from 'svelte';
	import type { MoodFace } from './liveRuns';

	interface Props {
		face?: MoodFace | null;
		/** Per-chip animation phase source (a run id). Same seed ⇒ same phase,
		 *  so a re-render doesn't make the face jump. */
		seed?: string;
		class?: string;
	}

	let { face = null, seed = '', class: klass = '' }: Props = $props();

	/** Still between flickers, and how fast the flicker itself runs. */
	const CALM_MS = 5000;
	const FLICKER_FRAME_MS = 190;

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

	let playing = $state<string | null>(null);
	let reduced = $state(false);

	onMount(() => {
		// Respect the OS switch: a mood is expressive, never urgent. Held
		// still, the resting face still carries the handle beside it, which is
		// the whole fact — the motion was only ever the delivery.
		const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
		reduced = !!mq?.matches;
		const onChange = (e: MediaQueryListEvent) => (reduced = e.matches);
		mq?.addEventListener?.('change', onChange);

		let timer: ReturnType<typeof setTimeout> | undefined;
		let cancelled = false;
		let cycle = 0;

		/** One flicker: walk a sequence frame by frame, then go still again
		 *  and queue the next one from a different alternate. */
		function step(frameIndex: number) {
			if (cancelled) return;
			const cycles = sequences;
			if (reduced || !cycles || cycles.length === 0) {
				playing = null;
				timer = setTimeout(() => step(0), CALM_MS);
				return;
			}
			const seq = cycles[cycle % cycles.length];
			if (frameIndex >= seq.length) {
				playing = null; // back to rest
				cycle += 1;
				timer = setTimeout(() => step(0), CALM_MS);
				return;
			}
			playing = seq[frameIndex];
			timer = setTimeout(() => step(frameIndex + 1), FLICKER_FRAME_MS);
		}

		// Start calm, offset per chip, so the grid never flickers in chorus.
		timer = setTimeout(() => step(0), CALM_MS + phaseOf(seed || face?.name || ''));
		return () => {
			cancelled = true;
			if (timer) clearTimeout(timer);
			mq?.removeEventListener?.('change', onChange);
		};
	});

	// The animating frame when one is playing, else the resting face, else
	// nothing. The name always rides along: an unknown handle has no face at
	// all, and the bare name is the honest render of that.
	let glyph = $derived(playing ?? restGlyph);
</script>

{#if face}
	{@const label = glyph ? `${glyph} ${face.name}` : face.name}
	<span
		class="min-w-0 shrink truncate font-mono text-[9px] text-ink-mute {klass}"
		title="mood: {face.name}"
	>
		{label}
	</span>
{/if}
