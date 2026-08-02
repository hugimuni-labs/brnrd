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
