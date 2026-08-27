/**
 * TypeScript port of `media/brand/build.py` and `media/brand/hugimuni/build.py`
 * — dependency-free Python that draws the brnrd and hugimuni marks as SVG
 * from a block of named constants. Source lives on branch
 * `brr/five-slots-and-the-middle-three-are-the-state` (PR #1488), not yet on
 * `main`; that branch also carries ~40 rendered `.svg`/`.png` assets this
 * task has no reason to pull in, so the constants and formulas are vendored
 * here rather than the branch merged. Every function below is a faithful
 * transcription — same names, same arithmetic — so a value tuned on
 * `/brand-bench` types straight back into `build.py` and reproduces the
 * identical mark.
 *
 * Three departures from a literal line-for-line port, none of them formula
 * changes:
 *
 * 1. Python's `SLOTS`, `STEMS`, `BAR_H` are computed once at module import.
 *    Here they're recomputed from the live constants on every call — the
 *    whole point of the bench is redraw-on-drag, so nothing can be baked in.
 * 2. `XTOP = BOWL_TOP` in Python is a one-time assignment (moving `BOWL_TOP`
 *    afterward doesn't move `XTOP`). The bench spec asks for XTOP as its own
 *    slider, so it's tracked as a fully independent constant here, defaulted
 *    to `BOWL_TOP`'s own default value (308).
 * 3. Python's `AXIS = BOARD / 2` is a float (`256.0`), and every coordinate
 *    derived from it downstream (`SLOTS`, stave x-positions, `vee_m`'s
 *    endpoints) prints with a trailing `.0` when interpolated into an f-string
 *    — e.g. `M 72.0 92 V 420`. JS has one numeric type, so the same
 *    arithmetic here prints `M 72 92 V 420`: numerically identical, cosmetically
 *    different. This never reaches the copy-constants pasteback (that block
 *    emits only the named constants, not derived path data), so it doesn't
 *    break the "paste it back into build.py" contract — noted here because it
 *    is the one place a byte-for-byte diff against the Python's own SVG output
 *    would show a difference that isn't a bug.
 */

export const BOARD = 512;
const AXIS = BOARD / 2;

export type Crown = 'none' | 'branch' | 'fork';
export type FaceName = 'rest' | 'up' | 'kawaii' | 'wide' | 'flat' | 'grip';
export type BrnrdFrame = FaceName | 'name';
export type GlyphKind = 'dot' | 'lown' | 'peak' | 'ring' | 'dash' | 'bar' | 'grit';

export interface BrnrdConstants {
	SLOT: number;
	STAVE_TOP: number;
	BASELINE: number;
	BOWL_TOP: number;
	BOWL_W: number;
	STAVE_INSET: number;
	STROKE: number;
	XTOP: number;
	EYE_Y: number;
	MOUTH_Y: number;
	EYE_R: number;
	CROWN: Crown;
}

export const BRNRD_DEFAULTS: BrnrdConstants = {
	SLOT: 80,
	STAVE_TOP: 92,
	BASELINE: 420,
	BOWL_TOP: 308,
	BOWL_W: 62,
	STAVE_INSET: 24,
	STROKE: 22,
	XTOP: 308,
	EYE_Y: 322,
	MOUTH_Y: 390,
	EYE_R: 15,
	CROWN: 'none'
};

// FACES: the Python dict currently carries six states, not the four the
// dispatching task text names — `rest`/`up` plus `kawaii`, `wide`, `flat`,
// `grip`. All six are ported (the "beyond the four" decision the task left
// open): they're free, being plain data, and the bench is more useful
// showing what the branch actually has than a stale subset. See the run
// report for the "beyond the four" call.
export const FACES: Record<FaceName, [GlyphKind, GlyphKind, GlyphKind]> = {
	rest: ['dot', 'bar', 'dot'],
	up: ['peak', 'bar', 'peak'],
	kawaii: ['peak', 'lown', 'peak'],
	wide: ['ring', 'bar', 'ring'],
	flat: ['dash', 'bar', 'dash'],
	grip: ['dot', 'grit', 'dot']
};

export const BRNRD_COLORS = {
	STONE: '#0c0906',
	MOLTEN: '#ff9a1f',
	EMBER: '#ff6a00',
	CREAM: '#f2ece1',
	RED: '#ff3b30',
	CYAN: '#3ad8e6'
};

/** SLOTS = [AXIS + (i - 2) * SLOT for i in range(5)] */
export function slots(slot: number): number[] {
	return [0, 1, 2, 3, 4].map((i) => AXIS + (i - 2) * slot);
}

function crownPath(x: number, staveTop: number, crown: Crown): string {
	if (crown === 'none') return '';
	if (crown === 'fork') {
		const arm = 32;
		return `
    <path d="M ${x - arm} ${staveTop + 40} H ${x + arm}"/>
    <path d="M ${x - arm} ${staveTop + 40} V ${staveTop + 8}"/>
    <path d="M ${x + arm} ${staveTop + 40} V ${staveTop + 8}"/>`;
	}
	const arm = 38;
	const rise = 54;
	return `
    <path d="M ${x} ${staveTop + rise} L ${x - arm} ${staveTop + 4}"/>
    <path d="M ${x} ${staveTop + rise} L ${x + arm} ${staveTop + 4}"/>`;
}

export function stavePath(x: number, flip: number, c: BrnrdConstants): string {
	const sweep = flip < 0 ? 1 : 0;
	const ry = (c.BASELINE - c.BOWL_TOP) / 2;
	return `
    <path d="M ${x} ${c.STAVE_TOP} V ${c.BASELINE}"/>${crownPath(x, c.STAVE_TOP, c.CROWN)}
    <path d="M ${x - 24} ${c.STAVE_TOP + 104} H ${x + 24}"/>
    <path d="M ${x - 24} ${c.STAVE_TOP + 144} H ${x + 24}"/>
    <path d="M ${x} ${c.BOWL_TOP} a ${c.BOWL_W} ${ry} 0 0 ${sweep} 0 ${c.BASELINE - c.BOWL_TOP}"/>`;
}

export function glyph(kind: GlyphKind, x: number, c: BrnrdConstants): string {
	switch (kind) {
		case 'dot':
			return `<circle cx="${x}" cy="${c.EYE_Y}" r="${c.EYE_R}" fill="url(#molten)" stroke="none"/>`;
		case 'lown': {
			// `b|^n^|d` with the n dropped — see build.py's own comment.
			const left = x - 26;
			const right = x + 26;
			const top = c.MOUTH_Y - 26;
			return (
				`<path d="M ${left} ${top + 14} V ${c.MOUTH_Y + 14}"/>` +
				`<path d="M ${right} ${top + 14} V ${c.MOUTH_Y + 14}"/>` +
				`<path d="M ${left} ${top + 14} L ${x} ${top} L ${right} ${top + 14}"/>`
			);
		}
		case 'peak':
			return `<path d="M ${x - 26} ${c.EYE_Y + 24} L ${x} ${c.EYE_Y} L ${x + 26} ${c.EYE_Y + 24}"/>`;
		case 'ring':
			return `<path d="M ${x} ${c.EYE_Y - 22} a 22 22 0 1 0 0.01 0" fill="none"/>`;
		case 'dash':
			return `<path d="M ${x - 20} ${c.EYE_Y} H ${x + 20}"/>`;
		case 'bar':
			return `<path d="M ${x - 48} ${c.MOUTH_Y} H ${x + 48}"/>`;
		case 'grit':
			return (
				`<path d="M ${x - 48} ${c.MOUTH_Y} H ${x + 48}"/>` +
				`<path d="M ${x - 48} ${c.MOUTH_Y - 28} H ${x + 48}"/>`
			);
	}
}

export function skeletonBody(face: FaceName, c: BrnrdConstants): string {
	const s = slots(c.SLOT);
	const parts = [stavePath(s[0] - c.STAVE_INSET, -1, c), stavePath(s[4] + c.STAVE_INSET, 1, c)];
	const kinds = FACES[face];
	const mids = s.slice(1, 4);
	kinds.forEach((kind, i) => parts.push(glyph(kind, mids[i], c)));
	return parts.join('');
}

export function letterR(x: number, mirror: boolean, c: BrnrdConstants): string {
	const s = mirror ? -1 : 1;
	const stem = x - s * 12;
	return `
    <path d="M ${stem} ${c.XTOP} V ${c.BASELINE}"/>
    <path d="M ${stem} ${c.XTOP + 4} L ${x + s * 26} ${c.XTOP - 20}"/>`;
}

export function letterN(x: number, c: BrnrdConstants): string {
	const left = x - 25;
	const right = x + 25;
	return `
    <path d="M ${left} ${c.XTOP} V ${c.BASELINE}"/>
    <path d="M ${right} ${c.XTOP + 14} V ${c.BASELINE}"/>
    <path d="M ${left} ${c.XTOP + 14} L ${x} ${c.XTOP} L ${right} ${c.XTOP + 14}"/>`;
}

export function restingBody(c: BrnrdConstants): string {
	const s = slots(c.SLOT);
	return [
		stavePath(s[0] - c.STAVE_INSET, -1, c),
		letterR(s[1], false, c),
		letterN(s[2], c),
		letterR(s[3], true, c),
		stavePath(s[4] + c.STAVE_INSET, 1, c)
	].join('');
}

export function brnrdBody(frame: BrnrdFrame, c: BrnrdConstants): string {
	return frame === 'name' ? restingBody(c) : skeletonBody(frame, c);
}

export function strokeAttrs(stroke: number): string {
	return `fill="none" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round"`;
}

/** Stone register: incised, molten, on rock. */
export function brnrdStoneSvg(frame: BrnrdFrame, c: BrnrdConstants): string {
	const body = brnrdBody(frame, c);
	const attrs = strokeAttrs(c.STROKE);
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${BOARD}" height="${BOARD}" viewBox="0 0 ${BOARD} ${BOARD}">
  <title>brnrd — the five-slot mark, stone register</title>
  <defs>
    <linearGradient id="molten" gradientUnits="userSpaceOnUse" x1="0" y1="80" x2="0" y2="430">
      <stop offset="0" stop-color="${BRNRD_COLORS.MOLTEN}"/>
      <stop offset="1" stop-color="${BRNRD_COLORS.EMBER}"/>
    </linearGradient>
    <filter id="heat" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="${BOARD}" height="${BOARD}" rx="112" fill="${BRNRD_COLORS.STONE}"/>
  <g ${attrs} stroke="url(#molten)" filter="url(#heat)">${body}
  </g>
</svg>
`;
}

/** Screen register: chromatic-aberration ghosts, the site's boot palette. */
export function brnrdAberrationSvg(frame: BrnrdFrame, c: BrnrdConstants): string {
	const body = brnrdBody(frame, c).replaceAll(
		'fill="url(#molten)"',
		`fill="${BRNRD_COLORS.CREAM}"`
	);
	const attrs = strokeAttrs(c.STROKE);
	const ghost = (colour: string, dx: number) =>
		`<g ${attrs} stroke="${colour}" opacity="0.55" transform="translate(${dx},0)">${body}</g>`;
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${BOARD}" height="${BOARD}" viewBox="0 0 ${BOARD} ${BOARD}">
  <title>brnrd — the five-slot mark, screen register</title>
  <rect width="${BOARD}" height="${BOARD}" rx="112" fill="${BRNRD_COLORS.STONE}"/>
  ${ghost(BRNRD_COLORS.RED, -7)}
  ${ghost(BRNRD_COLORS.CYAN, 7)}
  <g ${attrs} stroke="${BRNRD_COLORS.CREAM}">${body}</g>
</svg>
`;
}

/**
 * The Python constant block for the current brnrd values, ready to paste
 * over the top of `media/brand/build.py`.
 */
export function brnrdConstantBlock(c: BrnrdConstants): string {
	return [
		`SLOT = ${c.SLOT}`,
		`STAVE_TOP = ${c.STAVE_TOP}`,
		`BASELINE = ${c.BASELINE}`,
		`BOWL_TOP = ${c.BOWL_TOP}`,
		`BOWL_W = ${c.BOWL_W}`,
		`STAVE_INSET = ${c.STAVE_INSET}`,
		`STROKE = ${c.STROKE}`,
		`XTOP = ${c.XTOP}  # NB: build.py sets XTOP = BOWL_TOP by assignment —`,
		`                  #     paste this literal if XTOP now differs from BOWL_TOP`,
		`EYE_Y = ${c.EYE_Y}`,
		`MOUTH_Y = ${c.MOUTH_Y}`,
		`EYE_R = ${c.EYE_R}`,
		`CROWN = "${c.CROWN}"`
	].join('\n');
}

// ── hugimuni ─────────────────────────────────────────────────────────────

export interface HugimuniConstants {
	LEFT: number;
	RIGHT: number;
	TOP: number;
	BOTTOM: number;
	CROSS: number;
	OVERHANG: number;
	SPREAD: number;
	RISE: number;
	DIP: number;
	TAIL: number;
	STROKE: number;
	STEM_STROKE: number;
	GHOST: number;
	GRAIN: number;
	INTERSECTION: string;
	/** The contrast backdrop behind the glow (his 2026-08-28 read on the
	 *  transparent mark: "some of the image's background we actually need").
	 *  Flat and grainless — the grain belongs to the letters. GROUND_ON off
	 *  ⇒ fully transparent, the asset for dark surfaces that exist already. */
	GROUND_ON: boolean;
	GROUND: string;
	GROUND_RX: number;
}

export const HUGIMUNI_DEFAULTS: HugimuniConstants = {
	LEFT: 152,
	RIGHT: 360,
	TOP: 156,
	BOTTOM: 356,
	CROSS: 276,
	OVERHANG: 20,
	SPREAD: 20,
	RISE: 0,
	DIP: 0,
	TAIL: 20,
	STROKE: 28,
	STEM_STROKE: 40,
	GHOST: 7,
	GRAIN: 58,
	INTERSECTION: '#eadfca',
	GROUND_ON: true,
	GROUND: '#050705', // darker than the old #080b09 board — his "maybe darker"
	GROUND_RX: 64
};

export const HUGIMUNI_INK = '#080b09';

export type HugimuniPaletteName = 'amber-sky' | 'coral-turquoise';

export const HUGIMUNI_PALETTES: Record<HugimuniPaletteName, [string, string]> = {
	'amber-sky': ['#ff9a1f', '#69c7df'],
	'coral-turquoise': ['#ff6f61', '#3ec9bd']
};

export function hugimuniStems(c: HugimuniConstants): string {
	return `<path d="M ${c.LEFT} ${c.TOP} V ${c.BOTTOM}"/><path d="M ${c.RIGHT} ${c.TOP} V ${c.BOTTOM}"/>`;
}

export function hugimuniBarH(c: HugimuniConstants): string {
	return `<path d="M ${c.LEFT - c.OVERHANG} ${c.CROSS} H ${c.RIGHT + c.OVERHANG}"/>`;
}

export function hugimuniVeeM(c: HugimuniConstants): string {
	const drop = c.BOTTOM + c.DIP;
	return (
		`<path d="M ${c.LEFT - c.SPREAD} ${c.TOP - c.RISE} L ${AXIS + c.TAIL} ${drop}"/>` +
		`<path d="M ${c.RIGHT + c.SPREAD} ${c.TOP - c.RISE} L ${AXIS - c.TAIL} ${drop}"/>`
	);
}

export function hugimuniAttrs(stroke: number): string {
	return `fill="none" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round" style="mix-blend-mode:screen"`;
}

/**
 * The emissive render (reworked 2026-08-28, from his generated reference):
 * the mark carries its own light instead of sitting lit-from-nowhere on a
 * grained board. Three passes per stroke — wide bloom halo, saturated body,
 * thin blurred white-hot core — screen-blended inside one isolated group so
 * every crossing adds up and the intersections blaze. The grain moved from
 * the background onto the strokes: turbulence + scanlines clipped by a
 * stroke-shaped mask, overlay/multiply-blended so GRAIN modulates the letter
 * light itself (0 = clean neon, 100 = heavy phosphor fabric). No background
 * rect at all — the old rounded ink board read as a grained monitor bezel;
 * the mark is transparent now and sits on whatever ground the page gives it.
 */
export function hugimuniSvg(c: HugimuniConstants, paletteName: HugimuniPaletteName): string {
	const [a, b] = HUGIMUNI_PALETTES[paletteName];
	const stems = hugimuniStems(c);
	const bar = hugimuniBarH(c);
	const vee = hugimuniVeeM(c);
	const grain = Math.max(0, Math.min(100, c.GRAIN)) / 100;
	const coreW = (w: number) => Math.max(2, Math.round(w * 0.2));
	// one glyph, parameterized per pass: stroke widths scale, colours swap
	const glyph = (scale: (w: number) => number, aCol: string, bCol: string, coreCol: string) =>
		`<g ${hugimuniAttrs(scale(c.STEM_STROKE))} stroke="${aCol}" transform="translate(${-c.GHOST},0)">${stems}</g>` +
		`<g ${hugimuniAttrs(scale(c.STEM_STROKE))} stroke="${bCol}" transform="translate(${c.GHOST},0)">${stems}</g>` +
		`<g ${hugimuniAttrs(scale(Math.max(2, c.STEM_STROKE - c.GHOST * 2)))} stroke="${coreCol}">${stems}</g>` +
		`<g ${hugimuniAttrs(scale(c.STROKE))} stroke="${aCol}">${bar}</g>` +
		`<g ${hugimuniAttrs(scale(c.STROKE))} stroke="${bCol}">${vee}</g>`;
	const body = glyph((w) => w, a, b, c.INTERSECTION);
	const cores = glyph(coreW, '#fff6e4', '#eefbff', '#ffffff');
	// the mask: every stroke at full width, white — grain exists only where
	// the letters are
	const maskBody = glyph((w) => w, '#fff', '#fff', '#fff');
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${BOARD}" height="${BOARD}" viewBox="0 0 ${BOARD} ${BOARD}">
  <title>hugimuni — H and M on shared stems (${paletteName})</title>
  <defs>
    <filter id="hm-bloom" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="9"/>
    </filter>
    <filter id="hm-core" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="1.1"/>
    </filter>
    <filter id="hm-grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="3" seed="23"/>
      <feColorMatrix type="saturate" values="0"/>
    </filter>
    <pattern id="hm-scanlines" width="4" height="4" patternUnits="userSpaceOnUse">
      <path d="M0 3.5H4" stroke="#000" stroke-width="0.6" opacity="0.5"/>
    </pattern>
    <mask id="hm-strokes">
      <rect width="${BOARD}" height="${BOARD}" fill="#000"/>
      ${maskBody}
    </mask>
  </defs>
${c.GROUND_ON ? `  <rect width="${BOARD}" height="${BOARD}" rx="${c.GROUND_RX}" fill="${c.GROUND}"/>\n` : ''}  <g style="isolation:isolate">
    <g filter="url(#hm-bloom)" opacity="0.9">${body}</g>
    ${body}
    <g filter="url(#hm-core)" opacity="0.65">${cores}</g>
    <g mask="url(#hm-strokes)">
      <rect width="${BOARD}" height="${BOARD}" filter="url(#hm-grain)" opacity="${(grain * 0.6).toFixed(3)}" style="mix-blend-mode:overlay"/>
      <rect width="${BOARD}" height="${BOARD}" fill="url(#hm-scanlines)" opacity="${(grain * 0.55).toFixed(3)}" style="mix-blend-mode:multiply"/>
    </g>
  </g>
</svg>
`;
}

/**
 * The Python constant block for the current hugimuni values. `TAIL` is
 * local to `vee_m()` in `hugimuni/build.py`, not a module constant — the
 * bench exposes it as a slider because it's part of the geometry, but
 * pasting `TAIL = <n>` at module scope does nothing until `vee_m()` is
 * edited to read the module constant instead of its own local shadow. Said
 * plainly in the emitted comment rather than silently dropped.
 */
export function hugimuniConstantBlock(c: HugimuniConstants): string {
	return [
		`LEFT, RIGHT = ${c.LEFT}, ${c.RIGHT}`,
		`TOP, BOTTOM = ${c.TOP}, ${c.BOTTOM}`,
		`CROSS = ${c.CROSS}`,
		`OVERHANG = ${c.OVERHANG}`,
		`SPREAD = ${c.SPREAD}`,
		`RISE, DIP = ${c.RISE}, ${c.DIP}`,
		`STROKE = ${c.STROKE}`,
		`STEM_STROKE = ${c.STEM_STROKE}`,
		`GHOST = ${c.GHOST}`,
		`GRAIN = ${c.GRAIN}`,
		`INTERSECTION = "${c.INTERSECTION}"`,
		`GROUND_ON = ${c.GROUND_ON ? 'True' : 'False'}`,
		`GROUND = "${c.GROUND}"`,
		`GROUND_RX = ${c.GROUND_RX}`,
		'',
		'# TAIL lives inside vee_m() as a local, not up here — hand-edit that',
		`# function's "TAIL = ${c.TAIL}" line carries this value in build.py.`
	].join('\n');
}
