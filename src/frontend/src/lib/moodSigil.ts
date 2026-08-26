// The mood carved in stone — the sigil study (2026-08-26, his steer:
// "render the mood face isometrically … space-invader-ish … norse/
// vegvisir/stave inspired").
//
// The circle-visor put a UI chip in a world; a carving IS the world. This
// module turns one mood frame (the emote library's own `b<eye><mouth><eye>d`
// grammar) into a pixel grid a stele wears on its face:
//
// - **deterministic, never randomized** — same mood, same carving, or the
//   vocabulary can't be learned. Individuality lives in the stave lattice,
//   which hashes the frame text; the face rows encode the actual expression.
// - **invader-symmetric** — vertical mirror symmetry is what makes five
//   pixels read as a face; the lattice generates a left half and mirrors it.
// - the breath cycle re-derives the grid from the current frame, so the
//   carving shifts instead of text swapping — same license as before.
//
// Grid: SIGIL_COLS × SIGIL_ROWS. Row 0 crown lattice · rows 1-2 eyes ·
// row 3 gap · rows 4-5 mouth · rows 6-7 base lattice.

export const SIGIL_COLS = 7;
export const SIGIL_ROWS = 8;

type Cell2 = [boolean, boolean];
type Eye = [Cell2, Cell2];

/** 2×2 eye blocks keyed by the emote grammar's eye characters. The right
 *  eye renders mirrored, so slants stay symmetric like the faces are. */
const EYES: Record<string, Eye> = {
	'·': [
		[false, false],
		[true, false]
	],
	'.': [
		[false, false],
		[true, false]
	],
	'-': [
		[true, true],
		[false, false]
	],
	'=': [
		[true, true],
		[false, false]
	],
	o: [
		[true, true],
		[true, true]
	],
	O: [
		[true, true],
		[true, true]
	],
	'0': [
		[true, true],
		[true, true]
	],
	'^': [
		[false, true],
		[true, false]
	],
	ˋ: [
		[true, false],
		[false, true]
	],
	ˊ: [
		[true, false],
		[false, true]
	],
	x: [
		[true, false],
		[false, true]
	],
	X: [
		[true, false],
		[false, true]
	],
	'>': [
		[true, false],
		[false, true]
	],
	'<': [
		[true, false],
		[false, true]
	],
	_: [
		[false, false],
		[true, true]
	]
};
const EYE_FALLBACK: Eye = [
	[false, false],
	[true, false]
];

type MouthRow = [boolean, boolean, boolean];
type Mouth = [MouthRow, MouthRow];

const MOUTHS: Record<string, Mouth> = {
	_: [
		[false, false, false],
		[true, true, true]
	],
	'-': [
		[true, true, true],
		[false, false, false]
	],
	'=': [
		[true, true, true],
		[true, true, true]
	],
	w: [
		[true, false, true],
		[false, true, false]
	],
	o: [
		[true, true, true],
		[true, false, true]
	],
	O: [
		[true, true, true],
		[true, false, true]
	],
	'.': [
		[false, false, false],
		[false, true, false]
	],
	'^': [
		[false, true, false],
		[true, false, true]
	]
};
const MOUTH_FALLBACK: Mouth = [
	[false, false, false],
	[false, true, false]
];

/** FNV-1a — tiny, stable, endian-free; all we need is spread, not crypto. */
function fnv1a(text: string): number {
	let h = 0x811c9dc5;
	for (let i = 0; i < text.length; i++) {
		h ^= text.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h >>> 0;
}

/** The emote grammar's core: strip the `b…d` skull, expect eye·mouth·eye.
 *  Anything that doesn't parse carves the fallback face — never nothing,
 *  never a guess presented as the real expression (the lattice still
 *  hashes the true frame text, so unknown moods stay distinct). */
export function parseFaceCore(
	frame: string
): { left: string; mouth: string; right: string } | null {
	const core = [...frame.trim()];
	if (core.length < 3) return null;
	if (core[0] === 'b' && core[core.length - 1] === 'd') (core.splice(0, 1), core.splice(-1, 1));
	if (core.length !== 3) return null;
	return { left: core[0], mouth: core[1], right: core[2] };
}

/** A mirrored lattice row from hash bits — the stave filigree. */
function latticeRow(bits: number): boolean[] {
	const half = [(bits & 1) !== 0, (bits & 2) !== 0, (bits & 4) !== 0, (bits & 8) !== 0];
	return [half[0], half[1], half[2], half[3], half[2], half[1], half[0]];
}

/** One mood frame → the carving. Deterministic; symmetric where it must be. */
export function moodSigil(frame: string): boolean[][] {
	const grid: boolean[][] = Array.from({ length: SIGIL_ROWS }, () =>
		Array.from({ length: SIGIL_COLS }, () => false)
	);
	const hash = fnv1a(frame.trim());
	const face = parseFaceCore(frame);
	const leftEye = face ? (EYES[face.left] ?? EYE_FALLBACK) : EYE_FALLBACK;
	const rightEye = face ? (EYES[face.right] ?? EYE_FALLBACK) : EYE_FALLBACK;
	const mouth = face ? (MOUTHS[face.mouth] ?? MOUTH_FALLBACK) : MOUTH_FALLBACK;

	grid[0] = latticeRow(hash & 0xf);
	for (let r = 0; r < 2; r++) {
		for (let c = 0; c < 2; c++) {
			grid[1 + r][1 + c] = leftEye[r][c];
			// The right eye mirrors horizontally — slants read symmetric.
			grid[1 + r][4 + (1 - c)] = rightEye[r][c];
		}
	}
	for (let r = 0; r < 2; r++) {
		for (let c = 0; c < 3; c++) {
			grid[4 + r][2 + c] = mouth[r][c];
		}
	}
	grid[6] = latticeRow((hash >> 4) & 0xf);
	grid[7] = latticeRow((hash >> 8) & 0xf);
	return grid;
}
