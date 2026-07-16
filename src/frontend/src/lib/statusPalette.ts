// The dashboard's status language has two independent axes:
//
// - thermal tier = lifecycle temperature (where a thing is in its burn)
// - urgency = light (how strongly it needs attention now)
//
// Red is deliberately absent. It is reserved for a broken contract, such
// as a dispatched Core not matching the observed Core.

export const STATUS_BURNING = '#e8b34a';
export const STATUS_COOLING = '#a8cbdb';
export const STATUS_SPENT = '#9c8d7d';
export const STATUS_UNKNOWN = '#57534e';

/** @deprecated Use STATUS_BURNING. */
export const STATUS_AMPLE = STATUS_BURNING;
/** @deprecated Use STATUS_COOLING. */
export const STATUS_LOW = STATUS_COOLING;
/** @deprecated Use STATUS_SPENT. */
export const STATUS_CRITICAL = STATUS_SPENT;

/** Existing domain-neutral aliases remain source-compatible. */
export const STATUS_GOOD = STATUS_BURNING;
export const STATUS_WARN = STATUS_COOLING;

export type ThermalTier = 'burning' | 'cooling' | 'spent';
export type LegacyThermalTier = 'ample' | 'low' | 'critical';
export type StatusLevel = ThermalTier | LegacyThermalTier | 'unknown' | string;
export type GlowUrgency = 'calm' | 'attention' | 'alarm';
export type GlowShape = 'dot' | 'bar';

export interface ThermalStop {
	name: 'frost-deep' | 'frost' | 'pale-warm' | 'amber' | 'ember-ash' | 'ash';
	color: string;
}

/**
 * Discrete time scale for thawing countdowns and age-based ashing. Hue is
 * never interpolated: a direct frost→amber lerp crosses an unintended green.
 * Every stop clears WCAG AA normal-text contrast on the #0c0906 canvas.
 */
export const THERMAL_SCALE = [
	{ name: 'frost-deep', color: '#7897a5' },
	{ name: 'frost', color: STATUS_COOLING },
	{ name: 'pale-warm', color: '#c9b98f' },
	{ name: 'amber', color: STATUS_BURNING },
	{ name: 'ember-ash', color: '#b29274' },
	{ name: 'ash', color: STATUS_SPENT }
] as const satisfies readonly ThermalStop[];

export const THERMAL_STOPS = Object.fromEntries(
	THERMAL_SCALE.map((stop) => [stop.name, stop.color])
) as Record<ThermalStop['name'], string>;

function canonicalLevel(level: StatusLevel): ThermalTier | 'unknown' {
	if (level === 'ample') return 'burning';
	if (level === 'low') return 'cooling';
	if (level === 'critical') return 'spent';
	if (level === 'burning' || level === 'cooling' || level === 'spent') return level;
	return 'unknown';
}

export function urgencyForLevel(level: StatusLevel): GlowUrgency {
	const canonical = canonicalLevel(level);
	if (canonical === 'spent') return 'alarm';
	if (canonical === 'cooling') return 'attention';
	return 'calm';
}

/** Blend a hex color toward white without changing the tier's body color. */
export function glowTint(hex: string, ratio: number): string {
	const n = Number.parseInt(hex.slice(1), 16);
	const channels = [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
	const mix = (channel: number) => Math.round(channel + (255 - channel) * ratio);
	return `#${channels.map((channel) => mix(channel).toString(16).padStart(2, '0')).join('')}`;
}

/**
 * The sole owner of signal glow. Calm keeps a soft tier-color halo;
 * attention whitens and broadens it; alarm concentrates light at the
 * boundary so a dark body reads as the existing void treatment.
 */
export function glowFor(urgency: GlowUrgency, color: string, shape: GlowShape = 'dot'): string {
	if (urgency === 'calm') {
		return shape === 'bar'
			? `box-shadow: 0 0 6px 1px ${color}70, inset 0 0 3px rgba(255, 255, 255, 0.18);`
			: `box-shadow: 0 0 4px 1px ${color}70;`;
	}

	const light = glowTint(color, urgency === 'alarm' ? 0.72 : 0.55);
	if (urgency === 'attention') {
		return shape === 'bar'
			? `box-shadow: 0 0 9px 1px ${light}cc, inset 0 0 3px rgba(255, 255, 255, 0.25);`
			: `box-shadow: 0 0 6px 1px ${light}c0;`;
	}

	return shape === 'bar'
		? `box-shadow: 0 0 11px 1px ${light}dd, inset 0 0 3px rgba(0, 0, 0, 0.65);`
		: `box-shadow: 0 0 5px 1.5px ${light}, inset 0 0 2px ${color}80;`;
}

export function statusDotStyle(
	level: StatusLevel,
	color: string,
	urgency: GlowUrgency = urgencyForLevel(level)
): string {
	if (urgency === 'alarm') {
		return `background-color: #0c0906; border: 1px solid ${color}; ${glowFor(urgency, color)}`;
	}
	return `background-color: ${color}; ${glowFor(urgency, color)}`;
}

export function statusBarStyle(
	level: StatusLevel,
	color: string,
	urgency: GlowUrgency = urgencyForLevel(level)
): string {
	if (urgency === 'alarm') {
		return `background: linear-gradient(to right, #0c0906 0%, #0c0906 82%, ${color} 100%); ${glowFor(urgency, color, 'bar')}`;
	}
	return `background-color: ${color}; ${glowFor(urgency, color, 'bar')}`;
}
