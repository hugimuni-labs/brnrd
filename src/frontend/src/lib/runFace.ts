// A RUN HAS NO FACE (2026-08-02, his standing read: "the run is the center
// of the scene"). Layers got hues (#1014) because position could not carry
// identity; runs got nothing — a run appearing in the lane, the node, the
// cloth and the warp item that took it was four table entries the eye had to
// re-join by reading slugs. This module is the join: one persistent mark,
// derived from the run id alone, drawn wherever the run appears, so the eye
// can follow one run across surfaces and hours without reading anything.
//
// The mark is a rune plus a hue. Runes because the loom already speaks this
// cosmology (warp, weft, wyrd — and the resident's own substance is
// "networks etched in rune-stones"); a 24-glyph alphabet times a full hue
// wheel gives collisions no worse than any avatar system and a mark that
// stays legible at 10px in a mono font. Identity, not status: the hue says
// nothing about phase or health — the status palette keeps that job, and a
// face that warmed with progress would be two encodings of different
// quantities disagreeing on one glyph (#1014's armed-row lesson, inverted).
//
// Deterministic on purpose: same id, same face, every render, every device,
// no storage. FNV-1a over the id; glyph and hue read from different bit
// ranges so neighbouring ids differ in both.

export interface RunFace {
	/** One rune from the Elder Futhark — the run's glyph. */
	glyph: string;
	/** Identity hue, 0..359. */
	hue: number;
	/** The face's one CSS color: hue at fixed chroma, tuned for the stone
	 *  background. Every surface uses this same string, so the mark cannot
	 *  drift between renderers. */
	color: string;
}

/** The Elder Futhark, all 24 staves — visually distinct at small sizes,
 *  present in the runic block (U+16A0–16FF) that ships with the system
 *  fonts on macOS, iOS, Android and every mainstream Linux desktop. */
export const RUN_FACE_GLYPHS: readonly string[] = [
	'ᚠ',
	'ᚢ',
	'ᚦ',
	'ᚨ',
	'ᚱ',
	'ᚲ',
	'ᚷ',
	'ᚹ',
	'ᚺ',
	'ᚾ',
	'ᛁ',
	'ᛃ',
	'ᛇ',
	'ᛈ',
	'ᛉ',
	'ᛊ',
	'ᛏ',
	'ᛒ',
	'ᛖ',
	'ᛗ',
	'ᛚ',
	'ᛜ',
	'ᛞ',
	'ᛟ'
];

/** FNV-1a, 32-bit. Stable across platforms; no Math.random, no clock. */
function fnv1a(text: string): number {
	let hash = 0x811c9dc5;
	for (let i = 0; i < text.length; i += 1) {
		hash ^= text.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193) >>> 0;
	}
	return hash >>> 0;
}

/** The face for a run id. Any non-empty string works (event ids and wake ids
 *  get faces the same way); an empty id gets the zero face rather than a
 *  throw — a missing id is the caller's fact to render, not this module's
 *  crash. */
export function runFace(runId: string): RunFace {
	const hash = fnv1a(runId);
	const glyph = RUN_FACE_GLYPHS[hash % RUN_FACE_GLYPHS.length];
	// Different bit range than the glyph pick, so ids that collide on the
	// rune still usually part ways on the hue.
	const hue = Math.floor(hash / RUN_FACE_GLYPHS.length) % 360;
	return { glyph, hue, color: `hsl(${hue} 48% 64%)` };
}

function faceFor(hash: number, glyphIndex: number): RunFace {
	const glyph = RUN_FACE_GLYPHS[glyphIndex];
	const hue = Math.floor(hash / RUN_FACE_GLYPHS.length) % 360;
	return { glyph, hue, color: `hsl(${hue} 48% 64%)` };
}

/**
 * Faces for a rendered window, with in-window collision re-roll.
 *
 * `runFace` alone is honest about identity but not about legibility: 24
 * glyphs times however many runs render on a 30-day cloth means two runs
 * sharing a rune is the common case, not the rare one, and hue disambiguates
 * only in principle — nobody compares two hues from memory across a scroll of
 * rows. This function trades a sliver of that global honesty for local
 * legibility: within *this* window's contents, no two ids share a glyph
 * unless the window genuinely holds more distinct ids than the alphabet has
 * glyphs.
 *
 * The rule: walk `ids` in order (first occurrence wins — a repeated id keeps
 * whatever face its first appearance got, so one run drawn twice in one
 * window still reads as one run). Each id's hash still picks its *seed*
 * glyph and its hue — untouched, so a face that never collides is bit-for-bit
 * `runFace`'s answer. A seed glyph already claimed by an earlier id in this
 * same window probes forward (`seed+1`, `seed+2`, …, wrapping) until it finds
 * a free slot or has tried all 24 and found none.
 *
 * Overflow (more than 24 distinct ids in one window) makes some collision
 * unavoidable — pigeonhole, not a bug — and the probe loop's fallback is to
 * give the overflowing id back its *un-probed* seed glyph rather than
 * stealing a slot from a neighbour for a collision that was never going away
 * either way. Hue is what still tells the two apart at that point; this
 * function doesn't touch it, so the caller's existing hue rendering keeps
 * carrying that weight exactly as `runFace` already documents.
 *
 * Deliberately NOT a memoized global cache: same id, *different* window
 * contents (a new page of the cloth, a shorter pick lane after a run ends)
 * can legally re-roll to a different glyph. Stability holds only across
 * re-renders of the *same* window — the tradeoff the maintainer accepted,
 * in-window legibility over global stability. The run node page's solo view
 * has a window of one and never collides, which is why it keeps calling
 * `runFace` directly rather than this function.
 */
export function runFacesInWindow(ids: readonly string[]): Map<string, RunFace> {
	const out = new Map<string, RunFace>();
	const taken = new Set<number>();
	for (const id of ids) {
		if (out.has(id)) continue;
		const hash = fnv1a(id);
		const seed = hash % RUN_FACE_GLYPHS.length;
		let glyphIndex = seed;
		let probe = 0;
		while (taken.has(glyphIndex) && probe < RUN_FACE_GLYPHS.length) {
			probe += 1;
			glyphIndex = (seed + probe) % RUN_FACE_GLYPHS.length;
		}
		// Alphabet exhausted: every glyph is already spoken for in this window,
		// so this id collides with someone no matter what. Fall back to the
		// plain hash pick — the same glyph `runFace` alone would give it —
		// rather than the last-probed (arbitrary) slot.
		if (probe >= RUN_FACE_GLYPHS.length) glyphIndex = seed;
		taken.add(glyphIndex);
		out.set(id, faceFor(hash, glyphIndex));
	}
	return out;
}
