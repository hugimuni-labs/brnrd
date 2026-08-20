import { runnerBlocks } from '../railGauge.ts';
import type { LiveRun } from '../liveRuns.ts';
import type { QuotaShell } from '../quota.ts';
import type { RunnerProfile, RunnersResponse } from '../runners.ts';

export interface ShellBay {
	shell: string;
	profiles: RunnerProfile[];
	quota: QuotaShell | null;
	inUse: boolean;
	lastSeen: number;
}

export function dispatcherRun(runs: LiveRun[]): LiveRun | null {
	return (
		runs
			.filter((run) => !run.is_subspawn && !run.parent_run_id && !run.daemon_stale)
			.sort(
				(a, b) =>
					Date.parse(b.last_seen ?? b.started_at ?? '') -
					Date.parse(a.last_seen ?? a.started_at ?? '')
			)[0] ?? null
	);
}

export function handsFor(run: LiveRun, runs: LiveRun[]): LiveRun[] {
	return runs.filter(
		(candidate) => candidate.is_subspawn && candidate.parent_run_id === run.run_id
	);
}

export function nextProfile(
	runners: RunnersResponse | null,
	now = Date.now()
): RunnerProfile | null {
	if (!runners) return null;
	return (
		runnerBlocks(
			runners.profiles,
			runners.default,
			runners.wake_request,
			runners.sticky ?? null,
			now
		).find((block) => block.active)?.profile ?? null
	);
}

export function shellBays(
	runners: RunnersResponse | null,
	quotas: QuotaShell[],
	runs: LiveRun[],
	nowRun: LiveRun | null
): ShellBay[] {
	if (!runners) return [];
	const order = new Map<string, number>();
	const grouped = new Map<string, RunnerProfile[]>();
	for (const profile of runners.profiles) {
		const shell = profile.shell ?? 'unknown';
		if (!order.has(shell)) order.set(shell, order.size);
		grouped.set(shell, [...(grouped.get(shell) ?? []), profile]);
	}
	for (const quota of quotas) {
		if (!order.has(quota.shell)) order.set(quota.shell, order.size);
		if (!grouped.has(quota.shell)) grouped.set(quota.shell, []);
	}
	const inUseShell = nowRun?.runner.shell ?? null;
	return [...grouped]
		.map(([shell, profiles]) => ({
			shell,
			profiles,
			quota: quotas.find((quota) => quota.shell === shell) ?? null,
			inUse: shell === inUseShell,
			lastSeen: Math.max(
				0,
				...runs
					.filter((run) => run.runner.shell === shell)
					.map((run) => Date.parse(run.last_seen ?? '') || 0)
			)
		}))
		.sort(
			(a, b) =>
				Number(b.inUse) - Number(a.inUse) ||
				b.lastSeen - a.lastSeen ||
				(order.get(a.shell) ?? 0) - (order.get(b.shell) ?? 0)
		);
}
