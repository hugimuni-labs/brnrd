export const HUGIMUNI_BOARD = 512;
const AXIS = HUGIMUNI_BOARD / 2;
export const HUGIMUNI_LOCKUP_HEIGHT = 560;
export const HUGIMUNI_WORDMARK = 'HugiMuni';
export const HUGIMUNI_WORDMARK_SIZE = 42;
export const HUGIMUNI_WORDMARK_Y = 490;

export type HugimuniRegister = 'flat' | 'screen';

type Component = [number, number, number, number, number];

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
	AMBER: string;
	SKY: string;
	INTERSECTION: string;
	GROUND_ON: boolean;
	GROUND: string;
	GROUND_RX: number;
	BLOOM_BLUR: number;
	BLOOM_OPACITY: number;
	HOT_BLUR: number;
	HOT_OPACITY: number;
	GRAIN: number;
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
	AMBER: '#ff9a1f',
	SKY: '#69c7df',
	INTERSECTION: '#f0e3cf',
	GROUND_ON: true,
	GROUND: '#050705',
	GROUND_RX: 64,
	BLOOM_BLUR: 7,
	BLOOM_OPACITY: 0.42,
	HOT_BLUR: 1.4,
	HOT_OPACITY: 0.28,
	GRAIN: 22
};

function line(x1: number, y1: number, x2: number, y2: number, width: number): Component {
	return [x1, y1, x2, y2, width];
}

export function hugimuniHComponents(c: HugimuniConstants): Component[] {
	return [
		line(c.LEFT - c.GHOST, c.TOP, c.LEFT - c.GHOST, c.BOTTOM, c.STEM_STROKE),
		line(c.RIGHT - c.GHOST, c.TOP, c.RIGHT - c.GHOST, c.BOTTOM, c.STEM_STROKE),
		line(c.LEFT - c.OVERHANG, c.CROSS, c.RIGHT + c.OVERHANG, c.CROSS, c.STROKE)
	];
}

export function hugimuniMComponents(c: HugimuniConstants): Component[] {
	const drop = c.BOTTOM + c.DIP;
	return [
		line(c.LEFT + c.GHOST, c.TOP, c.LEFT + c.GHOST, c.BOTTOM, c.STEM_STROKE),
		line(c.RIGHT + c.GHOST, c.TOP, c.RIGHT + c.GHOST, c.BOTTOM, c.STEM_STROKE),
		line(c.LEFT - c.SPREAD, c.TOP - c.RISE, AXIS + c.TAIL, drop, c.STROKE),
		line(c.RIGHT + c.SPREAD, c.TOP - c.RISE, AXIS - c.TAIL, drop, c.STROKE)
	];
}

function componentSvg(component: Component, color: string): string {
	const [x1, y1, x2, y2, width] = component;
	return `<path d="M ${x1} ${y1} L ${x2} ${y2}" stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" fill="none"/>`;
}

function group(components: Component[], color: string): string {
	return components.map((component) => componentSvg(component, color)).join('');
}

function hMaskDef(c: HugimuniConstants, id: string): string {
	return `<mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}">
      <rect width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}" fill="#000"/>
      ${group(hugimuniHComponents(c), '#fff')}
    </mask>`;
}

function markMaskDef(c: HugimuniConstants, id: string): string {
	return `<mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}">
      <rect width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}" fill="#000"/>
      ${group(hugimuniHComponents(c), '#fff')}
      ${group(hugimuniMComponents(c), '#fff')}
    </mask>`;
}

function flatArt(c: HugimuniConstants, hMask: string, idPrefix?: string): string {
	const h = group(hugimuniHComponents(c), c.AMBER);
	const m = group(hugimuniMComponents(c), c.SKY);
	const overlap = group(hugimuniMComponents(c), c.INTERSECTION);
	const artId = idPrefix ? ` id="${idPrefix}-flat-art"` : '';
	const hId = idPrefix ? ` id="${idPrefix}-h-only"` : '';
	const mId = idPrefix ? ` id="${idPrefix}-m-only"` : '';
	const iId = idPrefix ? ` id="${idPrefix}-intersection"` : '';
	return `<g${artId}>
      <g${hId}>${h}</g>
      <g${mId}>${m}</g>
      <g${iId} mask="url(#${hMask})">${overlap}</g>
    </g>`;
}

function ground(c: HugimuniConstants, height: number): string {
	return c.GROUND_ON
		? `<rect width="${HUGIMUNI_BOARD}" height="${height}"${height === HUGIMUNI_BOARD ? ` rx="${c.GROUND_RX}"` : ''} fill="${c.GROUND}"/>`
		: '';
}

export function hugimuniFlatSvg(
	c: HugimuniConstants,
	idPrefix = 'hm-flat',
	height = HUGIMUNI_BOARD
): string {
	const hMask = `${idPrefix}-h`;
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${HUGIMUNI_BOARD}" height="${height}" viewBox="0 0 ${HUGIMUNI_BOARD} ${height}">
  <title>HugiMuni — canonical flat H/M mark</title>
  <defs>${hMaskDef(c, hMask)}</defs>
  ${ground(c, height)}
  ${flatArt(c, hMask, idPrefix)}
</svg>`;
}

export function hugimuniScreenSvg(c: HugimuniConstants, idPrefix = 'hm-screen'): string {
	const hMask = `${idPrefix}-h`;
	const markMask = `${idPrefix}-mark`;
	const bloom = `${idPrefix}-bloom`;
	const hotFilter = `${idPrefix}-hot`;
	const grainFilter = `${idPrefix}-grain`;
	const scanlines = `${idPrefix}-scanlines`;
	const grain = Math.max(0, Math.min(100, c.GRAIN)) / 100;
	const hot = group(hugimuniMComponents(c), '#fffaf1');
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}" viewBox="0 0 ${HUGIMUNI_BOARD} ${HUGIMUNI_BOARD}">
  <title>HugiMuni — emissive screen register</title>
  <defs>
    ${hMaskDef(c, hMask)}
    ${markMaskDef(c, markMask)}
    <filter id="${bloom}" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="${c.BLOOM_BLUR}"/></filter>
    <filter id="${hotFilter}" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="${c.HOT_BLUR}"/></filter>
    <filter id="${grainFilter}" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.72" numOctaves="2" seed="23"/><feColorMatrix type="saturate" values="0"/></filter>
    <pattern id="${scanlines}" width="4" height="4" patternUnits="userSpaceOnUse"><path d="M0 3.5H4" stroke="#000" stroke-width="0.55" opacity="0.42"/></pattern>
  </defs>
  ${ground(c, HUGIMUNI_BOARD)}
  <g style="isolation:isolate">
    <g filter="url(#${bloom})" opacity="${c.BLOOM_OPACITY}" style="mix-blend-mode:screen">${flatArt(c, hMask)}</g>
    ${flatArt(c, hMask, idPrefix)}
    <g mask="url(#${hMask})" filter="url(#${hotFilter})" opacity="${c.HOT_OPACITY}" style="mix-blend-mode:screen">${hot}</g>
    <g mask="url(#${markMask})">
      <rect width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}" filter="url(#${grainFilter})" opacity="${(grain * 0.34).toFixed(3)}" style="mix-blend-mode:overlay"/>
      <rect width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_BOARD}" fill="url(#${scanlines})" opacity="${(grain * 0.28).toFixed(3)}" style="mix-blend-mode:multiply"/>
    </g>
  </g>
</svg>`;
}

export function hugimuniLockupSvg(
	c: HugimuniConstants,
	register: HugimuniRegister,
	idPrefix = 'hm-lockup'
): string {
	const hMask = `${idPrefix}-h`;
	const mark = flatArt(c, hMask, idPrefix);
	if (register === 'flat') {
		return `<svg xmlns="http://www.w3.org/2000/svg" width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_LOCKUP_HEIGHT}" viewBox="0 0 ${HUGIMUNI_BOARD} ${HUGIMUNI_LOCKUP_HEIGHT}">
  <title>HugiMuni — canonical lockup</title>
  <defs>${hMaskDef(c, hMask)}</defs>
  ${ground(c, HUGIMUNI_LOCKUP_HEIGHT)}
  ${mark}
  <text x="${AXIS}" y="${HUGIMUNI_WORDMARK_Y}" text-anchor="middle" fill="${c.INTERSECTION}" font-family="Helvetica, Arial, sans-serif" font-size="${HUGIMUNI_WORDMARK_SIZE}" font-weight="400">${HUGIMUNI_WORDMARK}</text>
</svg>`;
	}

	// Keep the lockup word quiet; only the symbol receives the screen material.
	const markOnly = hugimuniScreenSvg({ ...c, GROUND_ON: false }, `${idPrefix}-screen`)
		.replace(/^<svg[^>]*>/, '')
		.replace(/<\/svg>$/, '');
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${HUGIMUNI_BOARD}" height="${HUGIMUNI_LOCKUP_HEIGHT}" viewBox="0 0 ${HUGIMUNI_BOARD} ${HUGIMUNI_LOCKUP_HEIGHT}">
  <title>HugiMuni — screen lockup</title>
  ${ground(c, HUGIMUNI_LOCKUP_HEIGHT)}
  ${markOnly}
  <text x="${AXIS}" y="${HUGIMUNI_WORDMARK_Y}" text-anchor="middle" fill="${c.INTERSECTION}" font-family="Helvetica, Arial, sans-serif" font-size="${HUGIMUNI_WORDMARK_SIZE}" font-weight="400">${HUGIMUNI_WORDMARK}</text>
</svg>`;
}

export function hugimuniConstantBlock(c: HugimuniConstants): string {
	return [
		`LEFT, RIGHT = ${c.LEFT}, ${c.RIGHT}`,
		`TOP, BOTTOM = ${c.TOP}, ${c.BOTTOM}`,
		`CROSS = ${c.CROSS}`,
		`OVERHANG = ${c.OVERHANG}`,
		`SPREAD = ${c.SPREAD}`,
		`RISE, DIP = ${c.RISE}, ${c.DIP}`,
		`TAIL = ${c.TAIL}`,
		`STROKE = ${c.STROKE}`,
		`STEM_STROKE = ${c.STEM_STROKE}`,
		`GHOST = ${c.GHOST}`,
		'',
		`AMBER = "${c.AMBER}"`,
		`SKY = "${c.SKY}"`,
		`INTERSECTION = "${c.INTERSECTION}"`,
		`GROUND = "${c.GROUND}"`,
		`GROUND_RX = ${c.GROUND_RX}`,
		'',
		`BLOOM_BLUR = ${c.BLOOM_BLUR}`,
		`BLOOM_OPACITY = ${c.BLOOM_OPACITY}`,
		`HOT_BLUR = ${c.HOT_BLUR}`,
		`HOT_OPACITY = ${c.HOT_OPACITY}`,
		`GRAIN = ${c.GRAIN}`
	].join('\n');
}
