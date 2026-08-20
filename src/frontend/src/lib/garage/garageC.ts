import { fuelRows } from '../railGauge.ts';
import type { LiveRun } from '../liveRuns.ts';
import type { QuotaShell } from '../quota.ts';
import { runnerBlocks } from '../railGauge.ts';
import type { RunnerProfile, RunnersResponse } from '../runners.ts';
import type { ScheduledWake } from '../scheduledWakes.ts';

export interface GarageShell {
	shell: string;
	profiles: RunnerProfile[];
	fuel: ReturnType<typeof fuelRows>;
	inUse: boolean;
	lastSeen: number;
	catalogOrder: number;
}

function stamp(value: string | null | undefined): number {
	const parsed = value ? Date.parse(value) : Number.NaN;
	return Number.isNaN(parsed) ? 0 : parsed;
}

export function garageNow(runs: LiveRun[]): LiveRun | null {
	return (
		[...runs]
			.filter((run) => !run.is_subspawn && !run.parent_run_id && !run.daemon_stale)
			.sort((a, b) => stamp(b.last_seen ?? b.started_at) - stamp(a.last_seen ?? a.started_at))[0] ??
		null
	);
}

export function garageHands(runs: LiveRun[], parent: LiveRun): LiveRun[] {
	return runs.filter((run) => run.is_subspawn && run.parent_run_id === parent.run_id);
}

export function garageNext(runners: RunnersResponse | null, now: number): RunnerProfile | null {
	if (!runners) return null;
	return (
		runnerBlocks(
			runners.profiles,
			runners.default,
			runners.wake_request,
			runners.sticky ?? null,
			now
		)[0]?.profile ?? null
	);
}

export function garageShells(
	runners: RunnersResponse | null,
	quota: QuotaShell[],
	runs: LiveRun[],
	nowRun: LiveRun | null
): GarageShell[] {
	if (!runners) return [];
	const order = new Map<string, number>();
	const profiles = new Map<string, RunnerProfile[]>();
	runners.profiles.forEach((profile, index) => {
		const shell = profile.shell ?? 'unknown';
		if (!order.has(shell)) order.set(shell, index);
		profiles.set(shell, [...(profiles.get(shell) ?? []), profile]);
	});
	const quotaByShell = new Map(quota.map((row) => [row.shell, row]));
	return [...profiles.entries()]
		.map(([shell, shellProfiles]) => ({
			shell,
			profiles: shellProfiles,
			fuel: fuelRows(quotaByShell.has(shell) ? [quotaByShell.get(shell)!] : []),
			inUse: nowRun?.runner.shell === shell,
			lastSeen: Math.max(
				0,
				...runs.filter((run) => run.runner.shell === shell).map((run) => stamp(run.last_seen))
			),
			catalogOrder: order.get(shell) ?? Number.MAX_SAFE_INTEGER
		}))
		.sort(
			(a, b) =>
				Number(b.inUse) - Number(a.inUse) ||
				b.lastSeen - a.lastSeen ||
				a.catalogOrder - b.catalogOrder
		);
}

export function nextWake(wakes: ScheduledWake[]): ScheduledWake | null {
	return (
		[...wakes]
			.filter((wake) => wake.scheduled_for && !Number.isNaN(Date.parse(wake.scheduled_for)))
			.sort((a, b) => stamp(a.scheduled_for) - stamp(b.scheduled_for))[0] ?? null
	);
}

export function compactCore(profile: RunnerProfile | null): string {
	if (!profile) return 'unavailable';
	const core = profile.model === 'default' || !profile.model ? profile.name : profile.model;
	return `${profile.shell ?? '?'}·${core.replace(/^gpt-/u, '')}`;
}
