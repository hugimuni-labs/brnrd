// THE GLYPH SUBSET — what a character grid may say and still be read.
//
// On a monospace board a glyph the reader's font lacks does not fail loudly.
// It substitutes, silently, from another face — and a *substituted* glyph is
// usually the wrong width too, so the grid shears. Measured on the
// maintainer's own screen, 2026-08-28, from two screenshots:
//
//   ⛁ (U+26C1, the garage) rendered as ⊕
//   ✉ (U+2709, the pager)  rendered as ≫
//   ⌁ (U+2301, the pulse)  rendered as ~
//
// The third is the expensive one. `⌁` marks the *boundary injection status*
// on the actor's line — the diegetic device the maintainer asked for three
// separate times, believing it unbuilt. It was built, it was rendering, and
// it was arriving as a tilde. **Implemented, rendered, unreadable** is a
// third state, and on a character grid it is a correctness concern rather
// than a matter of taste.
//
// The rule this file enforces is not a list of nice glyphs. It is: **a glyph
// is safe when the block it lives in is one a terminal font is built to
// cover.** Blocks, not characters, because a font ships blocks — and a rule
// stated over characters meets the character nobody listed.

/** Unicode ranges a monospace/terminal face is built to cover. */
const SAFE_BLOCKS: [number, number, string][] = [
	[0x20, 0x7e, 'ASCII'],
	[0xa0, 0xff, 'Latin-1 Supplement'],
	[0x2010, 0x2027, 'General Punctuation (dashes, quotes, ellipsis)'],
	[0x2190, 0x2199, 'Arrows — the eight cardinals only'],
	[0x2500, 0x257f, 'Box Drawing'],
	[0x2580, 0x259f, 'Block Elements'],
	// Stops at U+25CF: the run through the filled circle is what a terminal
	// face reliably ships. `◈` (U+25C8) sits inside it and is genuinely
	// common; the ornamental tail past U+25CF is not, and is excluded rather
	// than trusted.
	[0x25a0, 0x25cf, 'Geometric Shapes — squares, triangles, the common diamonds']
];

/**
 * Glyphs outside the safe blocks that the room emits **today**.
 *
 * This is a debt ledger, not a permission list. Every entry is a known
 * substitution risk, kept only because replacing it is a design decision
 * about what the mark should *be* — and that decision is the maintainer's.
 * The three marked `substituted` were observed falling back on a real
 * screen; the rest share a block with one that did, which is the whole
 * argument for reasoning in blocks.
 *
 * **Nothing may be added here without a decision.** The test that reads this
 * file exists so a new exotic glyph fails a check rather than quietly
 * arriving as a tilde on somebody's screen six weeks later.
 */
export const GLYPH_DEBT: Record<string, string> = {
	'⛁': 'U+26C1 · Misc Symbols · the garage — SUBSTITUTED as ⊕ on a real screen',
	'✉': 'U+2709 · Dingbats · the letter/pager — SUBSTITUTED as ≫ on a real screen',
	'⌁': 'U+2301 · Misc Technical · the boundary pulse — SUBSTITUTED as ~ on a real screen',
	'⌂': 'U+2302 · Misc Technical · HOME and island roots — same block as ⌁, but present in CP437 and therefore far more widely shipped',
	'☰': 'U+2630 · Misc Symbols · a read act — same block as ⛁',
	'✎': "U+270E · Dingbats · a write act — same block as ✉, which was seen falling back. Found by this file's own test on its first run, having been missed by the hand inventory that wrote this ledger — which is the argument for the test existing.",
	'∙': 'U+2219 · Math Operators · the current route',
	'≻': 'U+227B · Math Operators · the claw tip',
	'∿': 'U+223F · Math Operators · a tether frame',
	'≋': 'U+224B · Math Operators · a tether frame',
	'↻': 'U+21BB · Arrows, non-cardinal · a reset clock',
	'↳': 'U+21B3 · Arrows, non-cardinal · a strand under its parent'
};

/** True when every character of `text` is either inside a safe block or a
 *  declared, argued-for exception. */
export function unsafeGlyphs(text: string): string[] {
	const out = new Set<string>();
	for (const ch of text) {
		const code = ch.codePointAt(0) ?? 0;
		if (ch === '\n' || ch === '\t') continue;
		if (GLYPH_DEBT[ch]) continue;
		if (SAFE_BLOCKS.some(([lo, hi]) => code >= lo && code <= hi)) continue;
		out.add(ch);
	}
	return [...out];
}

/** Which safe block a character belongs to — for a failure message that
 *  says *why* rather than only *that*. */
export function blockOf(ch: string): string | null {
	const code = ch.codePointAt(0) ?? 0;
	return SAFE_BLOCKS.find(([lo, hi]) => code >= lo && code <= hi)?.[2] ?? null;
}
