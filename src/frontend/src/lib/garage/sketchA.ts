import { quotaWindowReading, type QuotaShell } from '../quota.ts';
import { liveSticky, type RunnerProfile, type RunnersResponse } from '../runners.ts';
import type { LiveRun } from '../liveRuns.ts';

export interface GarageShellRow {
	shell: string;
	profiles: RunnerProfile[];
	fuel: GarageFuel;
	inUse: boolean;
	lastSeen: number;
}

export interface GarageFuel {
	session: number | null;
	week: number | null;
}

function stamp(value: string | null | undefined): number {
	const parsed = value ? Date.parse(value) : Number.NaN;
	return Number.isNaN(parsed) ? 0 : parsed;
}

export function dispatcherRun(runs: readonly LiveRun[]): LiveRun | null {
	return (
		[...runs]
			.filter((run) => !run.is_subspawn && !run.parent_run_id && !run.daemon_stale)
			.sort((a, b) => stamp(b.last_seen ?? b.started_at) - stamp(a.last_seen ?? a.started_at))[0] ??
		null
	);
}

export function handsFor(runs: readonly LiveRun[], parent: LiveRun | null): LiveRun[] {
	if (!parent) return [];
	return runs
		.filter((run) => run.is_subspawn && run.parent_run_id === parent.run_id && !run.daemon_stale)
		.sort((a, b) => stamp(a.started_at) - stamp(b.started_at));
}

export function nextProfile(
	runners: RunnersResponse | null,
	now = Date.now()
): RunnerProfile | null {
	if (!runners) return null;
	const name =
		runners.wake_request?.profile ?? liveSticky(runners.sticky, now)?.profile ?? runners.default;
	return runners.profiles.find((profile) => profile.name === name) ?? null;
}

export function shellFuel(shell: QuotaShell | undefined): GarageFuel {
	const find = (names: string[]) =>
		shell?.windows.find((window) => names.includes(window.label.toLowerCase()));
	const session = find(['5h', 'session']);
	const week = find(['week', 'weekly', '7d']);
	return {
		session: session ? quotaWindowReading(session).percent : null,
		week: week ? quotaWindowReading(week).percent : null
	};
}

export function shellRows(
	profiles: readonly RunnerProfile[],
	quotas: readonly QuotaShell[],
	runs: readonly LiveRun[],
	nowRun: LiveRun | null
): GarageShellRow[] {
	const catalogOrder: string[] = [];
	const grouped = new Map<string, RunnerProfile[]>();
	for (const profile of profiles) {
		const shell = profile.shell ?? 'unknown';
		if (!grouped.has(shell)) catalogOrder.push(shell);
		grouped.set(shell, [...(grouped.get(shell) ?? []), profile]);
	}
	const inUseShell = nowRun?.runner.shell ?? null;
	return catalogOrder
		.map((shell) => ({
			shell,
			profiles: grouped.get(shell) ?? [],
			fuel: shellFuel(quotas.find((quota) => quota.shell === shell)),
			inUse: shell === inUseShell,
			lastSeen: Math.max(
				0,
				...runs.filter((run) => run.runner.shell === shell).map((run) => stamp(run.last_seen))
			)
		}))
		.sort((a, b) => {
			if (a.inUse !== b.inUse) return a.inUse ? -1 : 1;
			if (a.lastSeen !== b.lastSeen) return b.lastSeen - a.lastSeen;
			return catalogOrder.indexOf(a.shell) - catalogOrder.indexOf(b.shell);
		});
}

export function compactPercent(value: number | null): string {
	return value === null ? '?' : `${Math.round(value)}%`;
}

export function runSeconds(run: LiveRun | null, now: number): string {
	if (!run?.started_at) return '—';
	return `${Math.max(0, Math.floor((now - stamp(run.started_at)) / 1000))}s`;
}
