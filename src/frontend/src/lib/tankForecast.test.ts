import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
	readTank,
	readTanks,
	scheduledCost,
	tokensPerWeeklyPercent,
	wakesBefore
} from './tankForecast.ts';
import type { QuotaShell, QuotaWindow } from './quota.ts';
import type { RunLedgerRow } from './runLedger.ts';
import type { ScheduledWake } from './scheduledWakes.ts';

const NOW = Date.UTC(2026, 6, 19, 12, 0, 0);
const HOUR = 3600;

/** node:test has no `toBeCloseTo`; float comparisons need an explicit
 *  tolerance rather than an equality that passes only by luck of binary
 *  representation. */
function assertClose(actual: number | null | undefined, expected: number, digits: number) {
	assert.ok(actual !== null && actual !== undefined, 'expected a number, got null/undefined');
	const tolerance = Math.pow(10, -digits) / 2;
	assert.ok(
		Math.abs(actual - expected) < tolerance,
		`expected ${actual} to be within ${tolerance} of ${expected}`
	);
}

function row(over: Partial<RunLedgerRow> = {}): RunLedgerRow {
	return {
		run_id: 'run-x',
		event_id: null,
		started_at: null,
		ended_at: null,
		wall_clock_seconds: null,
		runner_shell: 'claude',
		runner_core: null,
		core_expected: null,
		core_mismatch: null,
		substitution_reason: null,
		repo_label: null,
		source_system: 'telegram',
		name: null,
		external_refs: null,
		task_classification: null,
		parent_run_id: null,
		is_subspawn: null,
		tokens_input: null,
		tokens_output: null,
		tokens_cache_read: null,
		tokens_cache_creation: null,
		context_window_used: null,
		weekly_pct_delta: null,
		five_hour_pct_delta: null,
		usd_subscription_attributed: null,
		usd_credits_equivalent: null,
		estimate_vs_actual: 'actual',
		...over
	};
}

function window(over: Partial<QuotaWindow> = {}): QuotaWindow {
	return { label: 'week', used: null, limit: null, percent: 50, reset: null, ...over };
}

function shell(windows: QuotaWindow[], status = 'known'): QuotaShell {
	return { shell: 'claude', status, windows };
}

function wake(scheduledFor: string | null): ScheduledWake {
	return {
		id: 'w',
		kind: 'scheduled',
		source: 'schedule',
		status: 'recurring',
		phase: 'every',
		bucket: 'future',
		summary: 'director tick',
		repo_label: null,
		daemon_name: null,
		conversation_key: null,
		scheduled_for: scheduledFor,
		reported_at: null
	};
}

describe('tokensPerWeeklyPercent', () => {
	it('calibrates from clean positive-delta rows', () => {
		const rows = [
			row({ tokens_input: 40000, tokens_output: 0, weekly_pct_delta: 1 }),
			row({ tokens_input: 100000, tokens_output: 0, weekly_pct_delta: 2 }),
			row({ tokens_input: 60000, tokens_output: 0, weekly_pct_delta: 1 }),
			row({ tokens_input: 90000, tokens_output: 0, weekly_pct_delta: 2 })
		];
		// ratios: 40k, 50k, 60k, 45k → median of the sorted middle pair.
		assert.equal(tokensPerWeeklyPercent(rows), 47500);
	});

	it('ignores the quantization floor: a 0 delta is "below resolution", not free', () => {
		const rows = [
			row({ tokens_input: 30000, weekly_pct_delta: 0 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 })
		];
		assert.equal(tokensPerWeeklyPercent(rows), 40000);
	});

	it('ignores negative deltas left on disk by the pre-fix ledger', () => {
		// `run_ledger._delta` nulls these at the source now, but rows written
		// before that fix still carry a window reset as a large negative — a
		// run *credited* for spending. Trusting one would invert the whole
		// calibration.
		const rows = [
			row({ tokens_input: 40000, weekly_pct_delta: -77 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 }),
			row({ tokens_input: 40000, weekly_pct_delta: 1 })
		];
		assert.equal(tokensPerWeeklyPercent(rows), 40000);
	});

	it('refuses to calibrate on too little evidence', () => {
		assert.equal(tokensPerWeeklyPercent([row({ tokens_input: 40000, weekly_pct_delta: 1 })]), null);
		assert.equal(tokensPerWeeklyPercent([]), null);
	});
});

describe('scheduledCost', () => {
	const calibration = [
		row({ tokens_input: 40000, weekly_pct_delta: 1 }),
		row({ tokens_input: 40000, weekly_pct_delta: 1 }),
		row({ tokens_input: 40000, weekly_pct_delta: 1 }),
		row({ tokens_input: 40000, weekly_pct_delta: 1 })
	];

	it('joins on source_system, which the daemon writes — not on the slug', () => {
		const rows = [
			...calibration,
			row({ source_system: 'schedule', tokens_input: 60000, task_classification: 'director-tick' }),
			row({ source_system: 'schedule', tokens_input: 80000, task_classification: 'director_tick' }),
			// Same shape of run, a slug nobody will ever join on again. It still
			// counts, because the join never looked at the slug.
			row({ source_system: 'schedule', tokens_input: 70000, task_classification: 'plan-review' }),
			row({ source_system: 'telegram', tokens_input: 900000 })
		];
		const cost = scheduledCost(rows);
		assert.equal(cost?.samples, 3);
		assert.equal(cost?.medianTokens, 70000);
		assertClose(cost?.percentOfWeek, 1.75, 5);
	});

	it('reports tokens but no percent when calibration is unavailable', () => {
		const rows = [
			row({ source_system: 'schedule', tokens_input: 60000 }),
			row({ source_system: 'schedule', tokens_input: 60000 }),
			row({ source_system: 'schedule', tokens_input: 60000 })
		];
		assert.equal(scheduledCost(rows)?.percentOfWeek, null);
	});

	it('is null below three scheduled samples', () => {
		assert.equal(scheduledCost([row({ source_system: 'schedule', tokens_input: 1 })]), null);
	});

	it('prices a shell against its own rows — a percent is not a shared unit', () => {
		// Claude's weekly window and codex's are different budgets, so a percent
		// of one is not a percent of the other. Measured on the live account
		// 2026-07-19: ~48,330 tokens per claude weekly percent against ~30,375
		// for codex. Blending them prices whichever window leads the strip with
		// a ratio dominated by whichever shell simply ran more often — here,
		// four claude calibration rows against one codex row.
		const rows = [
			...calibration, // claude, 40k tokens per weekly percent
			row({ runner_shell: 'codex', tokens_input: 20000, weekly_pct_delta: 1 }),
			row({ runner_shell: 'codex', tokens_input: 20000, weekly_pct_delta: 1 }),
			row({ runner_shell: 'codex', tokens_input: 20000, weekly_pct_delta: 1 }),
			row({ runner_shell: 'codex', tokens_input: 20000, weekly_pct_delta: 1 }),
			row({ runner_shell: 'codex', source_system: 'schedule', tokens_input: 60000 }),
			row({ runner_shell: 'codex', source_system: 'schedule', tokens_input: 60000 }),
			row({ runner_shell: 'codex', source_system: 'schedule', tokens_input: 60000 })
		];

		assert.equal(tokensPerWeeklyPercent(rows, 'claude'), 40000);
		assert.equal(tokensPerWeeklyPercent(rows, 'codex'), 20000);

		// The same 60k scheduled wake costs 1.5% of the claude week and 3% of
		// the codex week. Pricing it once, blended, would have split the
		// difference and been wrong for both.
		const codex = scheduledCost(rows, 'codex');
		assert.equal(codex?.samples, 3);
		assertClose(codex?.percentOfWeek, 3, 5);
	});

	it('yields no price for a shell with too few of its own rows', () => {
		// The fallback that must not exist: borrowing the other shell's ratio.
		// The caller shows the wake count with the cost omitted instead.
		const rows = [
			...calibration,
			row({ runner_shell: 'codex', source_system: 'schedule', tokens_input: 60000 }),
			row({ runner_shell: 'codex', source_system: 'schedule', tokens_input: 60000 }),
			row({ runner_shell: 'codex', source_system: 'schedule', tokens_input: 60000 })
		];
		assert.equal(scheduledCost(rows, 'codex')?.percentOfWeek, null);
	});

	it('matches shells case-insensitively across the two producers', () => {
		// The quota snapshot's `shell` and the ledger's `runner_shell` are
		// written by different producers; a casing drift must not silently
		// empty the calibration.
		assert.equal(tokensPerWeeklyPercent(calibration, 'Claude'), 40000);
	});
});

describe('wakesBefore', () => {
	it('counts only wakes with a known instant inside the horizon', () => {
		const wakes = [
			wake(new Date(NOW + 3600_000).toISOString()),
			wake(new Date(NOW + 7200_000).toISOString()),
			wake(new Date(NOW + 99 * 3600_000).toISOString()),
			// An `every:` entry still anchoring its first cycle: no instant, so
			// no draw. Guessing one would invent planned spend.
			wake(null)
		];
		assert.equal(wakesBefore(wakes, NOW, NOW + 10800_000), 2);
	});
});

describe('readTank', () => {
	it('measures the rate from the window itself, no ledger join', () => {
		// Half the 5h window elapsed, 40% drawn → 16%/h, projects to 20% at
		// reset. The provider reports both halves; nothing is derived from runs.
		const tank = readTank(
			shell([]),
			window({ label: '5h', percent: 60, resets_at: NOW / 1000 + 2.5 * HOUR }),
			0,
			NOW
		);
		assertClose(tank?.ratePerHour, 16, 5);
		assertClose(tank?.projectedRemainingAtReset, 20, 5);
		assert.equal(tank?.verdict, 'sustainable');
		assert.equal(tank?.exhaustsInHours, null);
	});

	it('calls a dry-out only when it lands before the refill', () => {
		// 90% drawn with 2.5h still to run: 36%/h against 10% left → dry in
		// ~17m, well inside the window.
		const tank = readTank(
			shell([]),
			window({ label: '5h', percent: 10, resets_at: NOW / 1000 + 2.5 * HOUR }),
			0,
			NOW
		);
		assert.equal(tank?.verdict, 'exhausting');
		assertClose(tank?.exhaustsInHours, 10 / 36, 4);
		assert.ok(String(tank?.headline).includes('dry in'));
	});

	it('treats a pace the window outlives as sustainable, not as an alarm', () => {
		// The reading the old flat bar never gave: 8% left is alarming as a
		// level and fine as a *rate* when the window refills in six minutes.
		const tank = readTank(
			shell([]),
			window({ label: '5h', percent: 8, resets_at: NOW / 1000 + 0.1 * HOUR }),
			0,
			NOW
		);
		assert.equal(tank?.exhaustsInHours, null);
		assert.notEqual(tank?.verdict, 'exhausting');
	});

	it('refuses to project from a window that barely started', () => {
		const tank = readTank(
			shell([]),
			window({ label: '5h', percent: 99, resets_at: NOW / 1000 + 4.9 * HOUR }),
			0,
			NOW
		);
		assert.equal(tank?.verdict, 'unknown');
		assert.equal(tank?.ratePerHour, null);
		assert.ok(String(tank?.headline).includes('too early'));
	});

	it('yields no rate for a window it cannot size', () => {
		const tank = readTank(
			shell([]),
			window({ label: 'monthly', percent: 40, resets_at: NOW / 1000 + HOUR }),
			0,
			NOW
		);
		assert.equal(tank?.elapsedFraction, null);
		assert.equal(tank?.ratePerHour, null);
		assert.equal(tank?.verdict, 'unknown');
	});

	it('is null for a window with no percent at all', () => {
		assert.equal(readTank(shell([]), window({ percent: null }), 0, NOW), null);
	});

	it('prices committed scheduled draw against the weekly window only', () => {
		const cost = { samples: 5, medianTokens: 70000, percentOfWeek: 1.5 };
		const weekly = readTank(
			shell([]),
			window({ label: 'week', percent: 65, resets_at: NOW / 1000 + 48 * HOUR }),
			0,
			NOW,
			{
				scheduledWakes: [
					wake(new Date(NOW + 5 * 3600_000).toISOString()),
					wake(new Date(NOW + 10 * 3600_000).toISOString())
				],
				scheduledCost: cost
			}
		);
		assert.equal(weekly?.committedWakes, 2);
		assertClose(weekly?.committedDraw, 3, 5);

		// A 5h window refills long before a recurring wake matters to it.
		const short = readTank(
			shell([]),
			window({ label: '5h', percent: 65, resets_at: NOW / 1000 + 2 * HOUR }),
			0,
			NOW,
			{ scheduledWakes: [wake(new Date(NOW + 3600_000).toISOString())], scheduledCost: cost }
		);
		assert.equal(short?.committedDraw, null);
	});

	it('omits committed draw rather than defaulting it when cost is unmeasured', () => {
		const tank = readTank(
			shell([]),
			window({ label: 'week', percent: 65, resets_at: NOW / 1000 + 48 * HOUR }),
			0,
			NOW,
			{
				scheduledWakes: [wake(new Date(NOW + 5 * 3600_000).toISOString())],
				scheduledCost: { samples: 3, medianTokens: 70000, percentOfWeek: null }
			}
		);
		assert.equal(tank?.committedWakes, 1);
		assert.equal(tank?.committedDraw, null);
	});

	it('carries the shell staleness through', () => {
		const tank = readTank(shell([], 'stale'), window({ resets_at: NOW / 1000 }), 0, NOW);
		assert.equal(tank?.stale, true);
	});
});

describe('readTanks', () => {
	it('puts the window that is about to run dry first', () => {
		const shells: QuotaShell[] = [
			shell([
				// sustainable
				window({ label: 'week', percent: 80, resets_at: NOW / 1000 + 3.5 * 86400 }),
				// exhausting
				window({ label: '5h', percent: 10, resets_at: NOW / 1000 + 2.5 * HOUR })
			])
		];
		const tanks = readTanks(shells, [], [], NOW);
		assert.deepEqual(tanks.map((t) => t.verdict), ['exhausting', 'sustainable']);
	});

	it('drops windows with no percent instead of rendering an empty track', () => {
		const shells: QuotaShell[] = [shell([window({ percent: null }), window({ percent: 50 })])];
		assert.equal((readTanks(shells, [], [], NOW)).length, 1);
	});
});
