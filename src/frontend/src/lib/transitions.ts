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

/** A fast, discrete-step "block assembly" reveal: left-to-right clip-path
 * steps plus a decaying horizontal jitter, snapped to whole frames instead
 * of continuously interpolated. Use on the element that appears when a
 * receipt expands (`in:glitchReveal`); pair with a plain `fade`/`slide` for
 * the collapse (`out:`) since a stop-motion *disappearance* reads as
 * flicker rather than a clean withdrawal. */
export function glitchReveal(_node: Element, params: GlitchRevealParams = {}) {
	const duration = params.duration ?? 180;
	const steps = Math.max(2, params.steps ?? 6);
	return {
		duration,
		css: (t: number) => {
			// Snap the continuous t onto `steps` discrete frames — this is
			// what makes it read as stop-motion rather than an eased eaxis.
			const frame = Math.min(1, Math.ceil(t * steps) / steps);
			const remaining = 1 - frame;
			// Jitter decays to 0 as the reveal completes; sin() gives a
			// settle-then-overshoot wobble instead of a monotonic slide.
			const jitter = frame < 1 ? Math.sin(frame * 47) * remaining * 6 : 0;
			const clipRight = Math.round((1 - frame) * 100);
			return (
				`opacity: ${frame};` +
				`clip-path: inset(0 ${clipRight}% 0 0);` +
				`transform: translateX(${jitter.toFixed(2)}px);`
			);
		}
	};
}
