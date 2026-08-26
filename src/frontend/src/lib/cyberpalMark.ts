// The cyberpal — the wordmark as a being (2026-08-26, his voice note:
// "the r and r looks like a kawaii face … the b and d is kind of like
// headphones or antennas … sometimes it's b r n r d, and sometimes it
// glitches between the states … it sways like a chibi character. It's our
// tamagotchi moment.").
//
// `brandGeometry.ts` already carves both states of the same five-slot mark:
// `name` (b r n r d, the letters) and the face frames (r/r as eyes, n as
// mouth). This module adds what makes it *alive*, as pure data a component
// can play and a test can pin:
//
// - **frame → face**: the daemon's mood wire speaks the emote grammar
//   (`b<eye><mouth><eye>d`, `moodSigil.parseFaceCore`). The drawn being has
//   six faces. The mapping is deterministic and total — an unknown mood
//   rests, it never guesses.
// - **the choreography**: the face is the base state; every cycle the being
//   blinks, then glitches through its own name and settles back. Glitch
//   beats carry the chromatic-aberration ghosts; the long name hold is
//   clean, so the name is *readable*, not noise.
// - **the box**: a tight viewBox around the drawn strokes so the mark sits
//   in running text without the 512-board's dead margin.
//
// No clocks, no randomness — the component owns the timers, this module
// owns what plays.

import {
	BOARD,
	BRNRD_DEFAULTS,
	brnrdBody,
	slots,
	type BrnrdConstants,
	type BrnrdFrame,
	type FaceName
} from './brandGeometry.ts';
import { parseFaceCore } from './moodSigil.ts';

export interface Beat {
	frame: BrnrdFrame;
	/** How long this beat holds, ms. */
	hold: number;
	/** Glitch beats wear the red/cyan aberration ghosts. */
	glitch: boolean;
}

/** One full wink-cycle period, matching the old text wink's tempo. */
export const CYBERPAL_PERIOD_MS = 9000;
export const CYBERPAL_FIRST_MS = 1800;

const EYE_FACE: Record<string, FaceName> = {
	// joy family — lifted peaks
	'^': 'up',
	ˋ: 'up',
	ˊ: 'up',
	// wide-open — rings
	o: 'wide',
	O: 'wide',
	'0': 'wide',
	// lidded / level — dashes
	'-': 'flat',
	'=': 'flat',
	_: 'flat',
	// crossed / winking reads as effort
	x: 'grip',
	X: 'grip',
	'>': 'grip',
	'<': 'grip'
};

/**
 * The face a mood frame wears. `kawaii` is earned, not defaulted: lifted
 * eyes *and* an open/curled mouth. Level eyes with a doubled mouth is the
 * gritted working face. Everything unreadable rests — the fallback is the
 * being's neutral, never an impersonation of a mood it can't parse.
 */
export function faceForFrame(frame: string | null | undefined): FaceName {
	if (!frame) return 'rest';
	const core = parseFaceCore(frame);
	if (!core) return 'rest';
	const eye = EYE_FACE[core.left] ?? EYE_FACE[core.right] ?? null;
	if (
		eye === 'up' &&
		(core.mouth === 'w' || core.mouth === 'o' || core.mouth === 'O' || core.mouth === '^')
	) {
		return 'kawaii';
	}
	if (eye === 'flat' && core.mouth === '=') return 'grip';
	return eye ?? 'rest';
}

/**
 * The cycle the being plays once per period, from its current base face:
 * a blink (eyes shut is the `flat` face), a stuttered glitch into the
 * letters, the name held long enough to read, a stutter back. Start and
 * end return to the base — the component renders `null` (no beat) as the
 * resting base face, exactly like the text wink rendered its resting text.
 */
export function buildCycle(base: FaceName): Beat[] {
	const blink: FaceName = base === 'flat' ? 'rest' : 'flat';
	return [
		{ frame: blink, hold: 130, glitch: false },
		{ frame: base, hold: 420, glitch: false },
		{ frame: 'name', hold: 110, glitch: true },
		{ frame: base, hold: 90, glitch: true },
		{ frame: 'name', hold: 1250, glitch: false },
		{ frame: base, hold: 140, glitch: true }
	];
}

export interface MarkBox {
	x: number;
	y: number;
	w: number;
	h: number;
}

/**
 * Tight box around every stroke the mark can draw, crown included, with
 * half a stroke of breathing room — round caps paint half a STROKE past
 * the path on every side.
 */
export function markBox(c: BrnrdConstants): MarkBox {
	const s = slots(c.SLOT);
	const leftStave = s[0] - c.STAVE_INSET;
	const rightStave = s[4] + c.STAVE_INSET;
	// Rungs reach ±24 off a stave; the branch crown ±38, the fork ±32.
	const arm = Math.max(24, c.CROWN === 'branch' ? 38 : c.CROWN === 'fork' ? 32 : 0);
	const pad = c.STROKE / 2 + 4;
	const x = leftStave - arm - pad;
	const y = c.STAVE_TOP - pad;
	return {
		x,
		y,
		w: rightStave + arm + pad - x,
		h: c.BASELINE + pad - y
	};
}

/**
 * A frame's drawn body with the eye-dot fill re-inked for inline use.
 * `brandGeometry` fills dots from the stone register's `#molten` gradient;
 * inline in a page there is no such def, so the ink is explicit —
 * `currentColor` for the main body, a ghost colour for the aberration.
 */
export function inkedBody(frame: BrnrdFrame, c: BrnrdConstants, ink: string): string {
	return brnrdBody(frame, c).replaceAll('url(#molten)', ink);
}

/** The constants the living mark wears by default: the tuned brand values
 *  with the branch crown up — the antennas his brief names. */
export const CYBERPAL_DEFAULTS: BrnrdConstants = { ...BRNRD_DEFAULTS, CROWN: 'branch' };

/** One representative wire frame per drawn face — the bench uses these to
 *  preview the living mark in any face without inventing its own grammar.
 *  Each round-trips through `faceForFrame` (pinned by test). */
export const FACE_DEMO_FRAMES: Record<FaceName, string> = {
	rest: 'b·_·d',
	up: 'b^_^d',
	kawaii: 'b^w^d',
	wide: 'bo_od',
	flat: 'b-_-d',
	grip: 'b-=-d'
};

export { BOARD };
