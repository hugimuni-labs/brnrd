// Custom Svelte transitions for this dashboard's "assembled, not tweened"
// motion language (kb/design-run-relics.md §"Expansion animation"). Block
// assembly stays a Svelte transition (`glitchReveal`). Streaming text uses
// an action (`typeReveal`) because a transition CSS function cannot create
// and drive per-character DOM without reflow.
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
	/** Small phase offset for sibling elements entering as a chorus. */
	delay?: number;
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
	const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const duration = reduced ? 0 : (params.duration ?? 320);
	// `steps` now selects how many of the glitch states get visited (the
	// final settled frame is always included), so fewer steps = choppier.
	const steps = Math.max(2, Math.min(params.steps ?? GLITCH_FRAMES.length, GLITCH_FRAMES.length));
	return {
		delay: reduced ? 0 : (params.delay ?? 0),
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

export interface TypeRevealParams {
	/** Explicit text keeps action-owned spans synchronized with Svelte data. */
	text?: string;
	duration?: number;
	/** Primarily for deterministic composition; siblings otherwise self-stagger. */
	delay?: number;
}

export const TYPE_REVEAL_GLYPHS = ['░', '▒', '·', '—', '/', '∆'] as const;

export function typeRevealDuration(length: number): number {
	// 2× the original pacing (2026-07-16 steer: "make the text streaming
	// twice slower, and everywhere") — the reveal was slick but over before
	// the eye landed on it, especially outside the expand buttons.
	return Math.max(500, Math.min(1200, 360 + Math.max(0, length) * 16));
}

/** Log curve calibrated so about 60% is visible at 30% elapsed. */
export function typeRevealProgress(elapsedRatio: number): number {
	const t = Math.max(0, Math.min(1, elapsedRatio));
	return Math.log1p(12 * t) / Math.log(13);
}

interface RevealCell {
	target: HTMLSpanElement;
	scramble: HTMLSpanElement;
}

function buildRevealCells(node: HTMLElement, text: string): RevealCell[] {
	node.textContent = '';
	node.setAttribute('aria-label', text);
	const cells: RevealCell[] = [];
	const tokens = text.split(/(\s+)/u);

	for (const token of tokens) {
		if (!token) continue;
		if (/^\s+$/u.test(token)) {
			for (const character of token) {
				if (character === '\n') node.append(node.ownerDocument.createElement('br'));
				else node.append(node.ownerDocument.createTextNode(character));
			}
			continue;
		}

		const word = node.ownerDocument.createElement('span');
		word.style.whiteSpace = 'nowrap';
		word.setAttribute('aria-hidden', 'true');
		for (const character of Array.from(token)) {
			const cell = node.ownerDocument.createElement('span');
			const target = node.ownerDocument.createElement('span');
			const scramble = node.ownerDocument.createElement('span');
			cell.dataset.typeRevealCell = '';
			cell.style.display = 'inline-block';
			cell.style.position = 'relative';
			scramble.style.position = 'absolute';
			scramble.style.inset = '0 auto auto 0';
			scramble.style.pointerEvents = 'none';
			target.textContent = character;
			scramble.textContent = character;
			cell.append(target, scramble);
			word.append(cell);
			cells.push({ target, scramble });
		}
		node.append(word);
	}

	return cells;
}

/**
 * Per-character streaming reveal with a fixed-width scramble frontier.
 * The true text occupies every character cell from frame zero; opacity,
 * never DOM width, changes during the animation. Mounting an expanded block
 * creates a new action instance, so expansion naturally replays the reveal.
 */
export function typeReveal(node: HTMLElement, params: TypeRevealParams = {}) {
	let frame = 0;
	let timer = 0;
	let currentText = '';

	function settle(text: string) {
		node.textContent = text;
		node.removeAttribute('aria-label');
	}

	function start(next: TypeRevealParams) {
		cancelAnimationFrame(frame);
		clearTimeout(timer);
		const text = next.text ?? node.textContent ?? '';
		if (text === currentText && node.querySelector('[data-type-reveal-cell]')) return;
		currentText = text;

		if (!text || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
			settle(text);
			return;
		}

		const cells = buildRevealCells(node, text);
		const duration = next.duration ?? typeRevealDuration(cells.length);
		const delay = next.delay ?? Math.floor(Math.random() * 72);
		const startedAt = performance.now() + delay;

		const draw = (now: number) => {
			const elapsed = Math.max(0, now - startedAt);
			const ratio = Math.min(1, elapsed / duration);
			const visible = Math.floor(typeRevealProgress(ratio) * cells.length);
			const scrambleFrame = Math.floor(elapsed / 42);

			cells.forEach((cell, index) => {
				const revealed = index < visible || ratio === 1;
				const frontier = !revealed && index < visible + 3;
				cell.target.style.opacity = revealed ? '1' : '0';
				cell.scramble.style.opacity = frontier ? '0.72' : '0';
				if (frontier) {
					cell.scramble.textContent =
						TYPE_REVEAL_GLYPHS[(index + scrambleFrame) % TYPE_REVEAL_GLYPHS.length];
				}
			});

			if (ratio < 1) frame = requestAnimationFrame(draw);
		};

		timer = window.setTimeout(() => {
			frame = requestAnimationFrame(draw);
		}, delay);
	}

	start(params);
	return {
		update: start,
		destroy() {
			cancelAnimationFrame(frame);
			clearTimeout(timer);
		}
	};
}
