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

// Explicit extension: the frontend's tests run under node's own runner, which
// resolves this module's imports for real. Every other cross-module import in
// `src/lib` is `import type` and therefore erased before resolution, so this is
// the first one that had to be spelled the way node reads it.
import { whenBooted } from './boot.ts';

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
	/**
	 * This span's character offset within a *shared sweep*, and the sweep's
	 * total length. Set together (see `revealTimeline`) when one line of prose
	 * is split across several spans — a paragraph carrying links and bold, say.
	 *
	 * Without them each span runs its own curve, so a sentence made of four
	 * tokens reveals as four simultaneous mini-typewriters rather than one head
	 * crossing the line. With them every span solves the same global head
	 * position and takes its own slice, so the reveal is continuous across
	 * markup it does not own. This is what lets an `<a>` keep its href and
	 * still stream: `typeReveal` owns a plain span *inside* the link, never the
	 * link itself.
	 */
	offset?: number;
	total?: number;
}

/**
 * Character offsets for a run of sibling spans that should share one sweep.
 * Returns the per-span `{ offset, total }` params; `total` is identical across
 * the group, which is what makes the head position agree between them.
 */
export function revealTimeline(lengths: readonly number[]): { offset: number; total: number }[] {
	const total = lengths.reduce((sum, n) => sum + n, 0);
	let offset = 0;
	return lengths.map((n) => {
		const at = offset;
		offset += n;
		return { offset: at, total };
	});
}

/**
 * Deterministic per-cell, per-frame noise in [0, 1).
 *
 * Deliberately not `Math.random()`: the glitch is a *drawn* property of
 * (cell, frame), so it is reproducible, testable, and identical across the
 * spans of one shared sweep. Two adjacent characters in the same frame get
 * uncorrelated values, which is what makes the frontier read as static rather
 * than as a moving gradient.
 */
export function glitchNoise(index: number, frame: number): number {
	const mixed = Math.imul(index + 1, 0x9e3779b1) ^ Math.imul(frame + 1, 0x85ebca6b);
	return ((mixed >>> 8) & 0xffff) / 0x10000;
}

/**
 * How far behind the head a settled character can still be re-corrupted, and
 * how often it happens.
 *
 * The original reveal only ever scrambled *ahead* of the head: every cell went
 * garbage → true exactly once and stayed clean. That is a typewriter, and it
 * is why the motion read as "streaming" rather than "glitching" (maintainer,
 * 2026-07-19: "all the text reveal should better glitch"). A glitch is text
 * that has already landed briefly failing again. So a narrow trailing window
 * behind the head flickers back to a scramble glyph for single frames — dense
 * enough to read as instability, sparse enough that the text stays legible and
 * the line still resolves clean.
 */
export const AFTERSHOCK_REACH = 16;
export const AFTERSHOCK_ODDS = 0.09;

export function isAftershock(index: number, head: number, frame: number): boolean {
	const behind = head - index;
	if (behind <= 0 || behind > AFTERSHOCK_REACH) return false;
	return glitchNoise(index, frame) < AFTERSHOCK_ODDS;
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

/**
 * How many character cells ahead of the reveal head carry scramble glyphs.
 *
 * A fixed band is a length-dependent bug, which is what made the frontier
 * read as "stopped scrambling" on the run body (reported 2026-07-19). The
 * duration is capped at 1200ms, so the head's speed scales with the string:
 * on a 20-character label it crosses under 3 cells per frame and every
 * character passes through a fixed 3-cell band, but on a 600-character card
 * body it crosses 8–17, leaping clean over the band. Measured against a
 * 60fps frame budget, a fixed band scrambled 100% of a short label's cells
 * and only 36% of a 600-character body's — with a hard ceiling of 3 × 72
 * frames however long the text got.
 *
 * So the band is the head's own velocity: it spans exactly the cells the
 * head just crossed, floored at the original 3. Short strings are
 * byte-identical to the fixed band (their advance never reaches 3); long
 * ones scramble 93%+ of their cells at any length. The residual head cells
 * are the ones the log curve reveals on its first drawn frame — instant by
 * design, not skipped.
 */
export function frontierWidth(previousVisible: number, visible: number): number {
	return Math.max(3, visible - previousVisible);
}

/**
 * How many characters of a document get the reveal before the rest paints.
 *
 * The first cut of the corpus reveal was gated per *page*: "streaming a whole
 * document per character is noise", so the corpus browser opted out entirely
 * and only the short live surfaces streamed. The worry was real and the
 * granularity was wrong — it banned the motion instead of bounding it. A
 * reader starts at the top of a page, so the sweep only ever needs to cover
 * the first screenful; beyond that it is animation nobody is looking at,
 * paying real cost (one rAF loop and a few hundred style writes per block) on
 * pages that run to thousands of blocks.
 */
export const REVEAL_CHAR_BUDGET = 2600;

/** Per-block: does this block reveal, or paint? Order is reading order. */
export function revealBudgetMask(
	lengths: readonly number[],
	budget: number = REVEAL_CHAR_BUDGET
): boolean[] {
	let spent = 0;
	return lengths.map((n) => {
		if (spent >= budget) return false;
		spent += n;
		return true;
	});
}

/** Context key for the page-wide ledger. */
export const REVEAL_LEDGER = Symbol('reveal-ledger');

export interface RevealLedger {
	/** Reading-order mask for one document's blocks, spending the shared budget. */
	claim(key: string, lengths: readonly number[]): boolean[];
	/** Start a fresh page. */
	reset(): void;
}

/**
 * One reveal budget shared across every renderer on a page.
 *
 * A per-component budget is the wrong denominator and it was measured wrong:
 * the run node mounts ten `MarkdownContent`s (frame prose, produce, body, and
 * one per receipted message), each of which was individually under its own cap
 * and collectively built 14,500 animating character cells — 1.3s of long tasks
 * against a 61ms no-reveal baseline. A reader sees one page, so the page is
 * what has to be bounded. Ten small documents are not ten small pages.
 *
 * Claims are memoized on `key` + the document's shape because Svelte re-derives
 * freely — this dashboard re-renders on every live-runs poll — and a ledger
 * that charged twice for the same document would silently switch the motion off
 * a second after arrival. Order of first claim is mount order, which is reading
 * order, which is what makes "the opening of the page" the part that streams.
 */
export function revealLedger(budget: number = REVEAL_CHAR_BUDGET): RevealLedger {
	let issued = new Map<string, boolean[]>();
	let spent = 0;
	return {
		claim(key, lengths) {
			const id = `${key}#${lengths.length}#${lengths.reduce((sum, n) => sum + n, 0)}`;
			const cached = issued.get(id);
			if (cached) return cached;
			const mask = lengths.map((n) => {
				if (spent >= budget) return false;
				spent += n;
				return true;
			});
			issued.set(id, mask);
			return mask;
		},
		reset() {
			issued = new Map();
			spent = 0;
		}
	};
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
 * Does an action update carry new text, or is it the same string arriving
 * again?
 *
 * Load-bearing, and the source of a live bug (reported 2026-07-18: reveals
 * stopping half-way and leaving the scramble frontier glyphs on screen). The
 * action cancelled its in-flight frame *before* testing identity, so any
 * reactive update that re-ran it with unchanged text — a sibling poll, a
 * store tick, a parent re-render — killed the animation permanently and froze
 * whatever the last drawn frame happened to be.
 *
 * Identity is the whole test. A remount builds a fresh action instance with
 * an empty `currentText`, which is what makes an expanding block replay its
 * reveal; a same-text update inside one instance must never restart *or*
 * interrupt.
 */
export function shouldRestartReveal(currentText: string, nextText: string): boolean {
	return nextText !== currentText;
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
	let cancelBootWait: () => void = () => {};

	function settle(text: string) {
		node.textContent = text;
		node.removeAttribute('aria-label');
	}

	function start(next: TypeRevealParams) {
		const text = next.text ?? node.textContent ?? '';
		if (!shouldRestartReveal(currentText, text)) return;
		cancelAnimationFrame(frame);
		clearTimeout(timer);
		currentText = text;

		// `duration: 0` is the explicit opt-out, for a caller that must attach the
		// action unconditionally (Svelte's `use:` cannot be conditional) but has
		// decided this particular element should paint — a section heading past
		// the reveal budget, say. Settling here skips building the per-character
		// scaffolding at all, rather than building it and tearing it down a frame
		// later.
		if (
			!text ||
			next.duration === 0 ||
			window.matchMedia('(prefers-reduced-motion: reduce)').matches
		) {
			settle(text);
			return;
		}

		// Nothing is built until the boot curtain lifts. Building the cells first
		// and *then* waiting would leave the node holding per-character
		// scaffolding that no frame has drawn yet — target and scramble spans
		// both at their default opacity, i.e. the text rendered twice, overlapping
		// — for however long the wait lasts. Until the reveal can actually run,
		// the node keeps the plain text Svelte put there.
		let cells: RevealCell[] = [];
		const offset = next.offset ?? 0;
		const delay = next.delay ?? Math.floor(Math.random() * 72);
		let total = 0;
		let duration = 0;
		let startedAt = 0;
		let previousHead = 0;

		const draw = (now: number) => {
			const elapsed = Math.max(0, now - startedAt);
			const ratio = Math.min(1, elapsed / duration);
			const head = Math.floor(typeRevealProgress(ratio) * total);
			const scrambleFrame = Math.floor(elapsed / 42);
			const band = frontierWidth(previousHead, head);
			previousHead = head;

			cells.forEach((cell, index) => {
				// Positions are global to the sweep; the cell's local index is only
				// where it sits in this span.
				const at = index + offset;
				const settled = at < head;
				const shocked = ratio < 1 && settled && isAftershock(at, head, scrambleFrame);
				const revealed = ratio === 1 || (settled && !shocked);
				const frontier = !revealed && at < head + band;
				cell.target.style.opacity = revealed ? '1' : '0';
				if (frontier) {
					const noise = glitchNoise(at, scrambleFrame);
					cell.scramble.textContent =
						TYPE_REVEAL_GLYPHS[Math.floor(noise * TYPE_REVEAL_GLYPHS.length)];
					// Per-cell opacity and sub-pixel offset: the frontier stops
					// reading as one uniform grey band and starts reading as cells
					// individually failing. The scramble span is absolutely
					// positioned, so nudging it never reflows the line.
					cell.scramble.style.opacity = (shocked ? 0.85 : 0.45 + noise * 0.45).toFixed(2);
					cell.scramble.style.transform = `translate(${(noise - 0.5).toFixed(2)}px, ${(
						noise - 0.5
					).toFixed(2)}px)`;
				} else {
					cell.scramble.style.opacity = '0';
				}
			});

			// Settle on the last frame: collapse the per-character scaffolding
			// back to plain text. A finished reveal otherwise keeps every cell's
			// absolutely positioned scramble span in the DOM at opacity 0 — one
			// missed inline style away from being the frontier glyphs a reader
			// sees stranded at the end of a fully revealed string.
			if (ratio < 1) frame = requestAnimationFrame(draw);
			else settle(text);
		};

		// Held until the boot curtain lifts. The clock starts *then*, so a reveal
		// queued during boot still runs its full sweep rather than arriving
		// already half-elapsed.
		cancelBootWait();
		cancelBootWait = whenBooted(() => {
			cells = buildRevealCells(node, text);
			// A shared sweep measures the head against the *group's* length, not
			// this span's, so every span in the group agrees on where the head is.
			total = next.total ?? cells.length;
			duration = next.duration ?? typeRevealDuration(total);
			startedAt = performance.now() + delay;
			timer = window.setTimeout(() => {
				frame = requestAnimationFrame(draw);
			}, delay);
		});
	}

	start(params);
	return {
		update: start,
		destroy() {
			cancelBootWait();
			cancelAnimationFrame(frame);
			clearTimeout(timer);
		}
	};
}
