import type { QuotaShell, QuotaWindow } from './quota';
import type { RunnerProfile, WakeRequest } from './runners';

export type RunnerBlockKind = 'requested' | 'default';

export interface RunnerBlock {
	profile: RunnerProfile;
	kind: RunnerBlockKind;
	badge: 'requested · next wake' | 'default';
	active: boolean;
}

export interface FuelRow {
	id: string;
	label: string;
	percent: number | null;
	percentLabel: string;
	tooltip: string;
	stale: boolean;
}

/**
 * Reduces the rack to the one answer the header needs. A parked request is
 * foreground intent; the default remains visible only when it is genuinely a
 * different fallback, so duplicate blocks cannot imply two competing wakes.
 */
export function runnerBlocks(
	profiles: RunnerProfile[],
	defaultProfile: string | null,
	wakeRequest: WakeRequest | null
): RunnerBlock[] {
	const fallback =
		profiles.find((profile) => profile.name === defaultProfile) ??
		profiles.find((profile) => profile.selected === true);
	const requested = wakeRequest
		? profiles.find((profile) => profile.name === wakeRequest.profile)
		: undefined;

	if (requested) {
		const blocks: RunnerBlock[] = [
			{ profile: requested, kind: 'requested', badge: 'requested · next wake', active: true }
		];
		if (fallback && fallback.name !== requested.name) {
			blocks.push({ profile: fallback, kind: 'default', badge: 'default', active: false });
		}
		return blocks;
	}

	return fallback ? [{ profile: fallback, kind: 'default', badge: 'default', active: true }] : [];
}

function compactWindowName(window: QuotaWindow): { owner: string | null; window: string } {
	const modelWeek = /^weekly\s*\(([^)]+)\)$/iu.exec(window.label.trim());
	if (modelWeek) return { owner: modelWeek[1].trim().toLowerCase(), window: 'week' };

	return {
		owner: null,
		window: window.label
			.trim()
			.toLowerCase()
			.replace(/^weekly$/u, 'week')
			.replace(/\s+window$/u, '')
	};
}

function resetLabel(window: QuotaWindow): string | null {
	if (window.reset) return window.reset;
	if (window.resets_at === null || window.resets_at === undefined) return null;
	return `resets ${new Date(window.resets_at * 1000).toISOString()}`;
}

/**
 * The compact gauge follows the daemon's window list rather than naming four
 * product buckets in UI code. That keeps a changed provider window visible on
 * the very next report, while model-specific weekly pools still read as their
 * model (for example `fable · week`) instead of a misleading shell duplicate.
 */
export function fuelRows(shells: QuotaShell[]): FuelRow[] {
	return shells.flatMap((shell) =>
		shell.windows.map((window, index) => {
			const compact = compactWindowName(window);
			const owner = compact.owner ?? shell.shell.toLowerCase();
			const percent =
				window.percent === null || window.percent === undefined
					? null
					: Math.max(0, Math.min(100, window.percent));
			const percentLabel = percent === null ? '?' : `${Math.round(percent)}%`;
			const label = `${owner} · ${compact.window}`;
			const reset = resetLabel(window);

			return {
				id: `${shell.shell}:${window.label}:${index}`,
				label,
				percent,
				percentLabel,
				tooltip: `${label}: ${percent === null ? 'unknown' : `${Math.round(percent)}% left`}${reset ? ` · ${reset}` : ''}`,
				stale: shell.status === 'stale'
			};
		})
	);
}
