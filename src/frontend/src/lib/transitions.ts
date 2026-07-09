// Custom Svelte transitions for this dashboard's "assembled, not tweened"
// motion language (kb/design-run-relics.md §"Expansion animation").
//
// The maintainer's own framing, verbatim: the receipt expansion "should be
// like a stop-motion, glitchy, assembly of the block shape animation, quite
// fast to not disturb." A plain `fly`/`fade` (already used elsewhere on this
// dashboard, e.g. `LiveRuns.svelte`) reads as smooth interpolation — the
// opposite of what was asked for. `glitchReveal` snaps Svelte's continuous
// `t` (0→1) onto a small number of discrete frames, so the element appears
// to assemble in visible steps rather than ease in.

export interface GlitchRevealParams {
	/** Total transition time in ms. Kept short by design — "quite fast to
	 * not disturb" — so it reads as a flourish, not a wait. */
	duration?: number;
	/** How many discrete frames the reveal snaps through. More steps reads
	 * smoother (closer to a tween); fewer reads choppier/more stop-motion. */
	steps?: number;
}

// Deterministic per-frame glitch states (v2, 2026-07-09). The first cut
// (180ms, monotonic left→right wipe + fade) tested as invisible in practice
// — the maintainer's live verdict: "the animation is nowhere to be seen,
// it is just a dropdown thing." A wipe whose every frame is a superset of
// the previous one reads as a slide however you snap it. Stop-motion needs
// frames that *disagree* with each other: blocks appearing out of order,
// a brightness flicker, offsets that jump rather than decay smoothly.
// Each state is [opacity, clipTop%, clipRight%, clipBottom%, clipLeft%,
// translateX px, brightness]; the sequence assembles noisily and snaps
// clean on the final frame.
const GLITCH_FRAMES: ReadonlyArray<
	readonly [number, number, number, number, number, number, number]
> = [
	[0.4, 0, 62, 55, 0, -7, 1.6], // first flash: top-left block only
	[0.9, 30, 8, 0, 22, 5, 0.7], // jump: bottom band, dim
	[0.55, 0, 40, 20, 6, -4, 1.35], // flicker dip, most of left
	[1.0, 12, 0, 30, 0, 3, 1.15], // wide band, slight bright
	[0.75, 0, 18, 0, 10, -2, 0.85], // near-full, dim pulse
	[1.0, 0, 4, 6, 0, 1, 1.25], // one-frame bright pop
	[1.0, 0, 0, 0, 0, 0, 1.0] // settled
];

/** A discrete-frame "block assembly" reveal: the element flickers through
 * a fixed sequence of disagreeing clip/offset/brightness states — blocks
 * landing out of order — then snaps clean. Use on the element that appears
 * when a receipt expands (`in:glitchReveal`); pair with a plain `fade` for
 * the collapse (`out:`) since a stop-motion *disappearance* reads as
 * flicker rather than a clean withdrawal. */
export function glitchReveal(_node: Element, params: GlitchRevealParams = {}) {
	const duration = params.duration ?? 320;
	// `steps` now selects how many of the glitch states get visited (the
	// final settled frame is always included), so fewer steps = choppier.
	const steps = Math.max(2, Math.min(params.steps ?? GLITCH_FRAMES.length, GLITCH_FRAMES.length));
	return {
		duration,
		css: (t: number) => {
			// Snap continuous t onto the frame sequence — no interpolation
			// between states; the disagreement between frames IS the effect.
			const idx = Math.min(
				GLITCH_FRAMES.length - 1,
				GLITCH_FRAMES.length - steps + Math.floor(t * steps)
			);
			const [o, ct, cr, cb, cl, tx, br] = GLITCH_FRAMES[idx];
			return (
				`opacity: ${o};` +
				`clip-path: inset(${ct}% ${cr}% ${cb}% ${cl}%);` +
				`transform: translateX(${tx}px);` +
				`filter: brightness(${br});`
			);
		}
	};
}
