import assert from 'node:assert/strict';
import test from 'node:test';

import {
	availabilityOf,
	deadShellReason,
	defaultShell,
	groupByShell,
	isTappable,
	offerabilityOf,
	offReasonOf,
	headline,
	orderRows,
	tierLabel
} from './spoolRack.ts';
import type { RunnerProfile } from './runners.ts';

test('availability fails closed — a row missing the field is unverified, not available', () => {
	assert.equal(availabilityOf({ name: 'x' }), 'unverified');
	assert.equal(availabilityOf({ name: 'x', available: null }), 'unverified');
	assert.equal(availabilityOf({ name: 'x', available: true }), 'available');
	assert.equal(availabilityOf({ name: 'x', available: false }), 'unavailable');
});

test('a row can only tap when verified available and no report — account or row — is stale', () => {
	const live: RunnerProfile = { name: 'claude', available: true };
	assert.equal(isTappable(live, false), true);
	assert.equal(isTappable(live, true), false, 'account-wide stale report disables every row');
	assert.equal(
		isTappable({ ...live, daemon_stale: true }, false),
		false,
		"a row's own stale source report disables it even when the account-wide chip is green"
	);
	assert.equal(isTappable({ name: 'unverified' }, false), false, 'unverified never taps');
	assert.equal(
		isTappable({ name: 'dead', available: false }, false),
		false,
		'unavailable never taps'
	);
});

// w-68 (2026-08-19), his mid-flight steer: "stale must never reach the
// reader" — a row is offerable or it is off, never a third state. These pin
// the binary itself, distinct from `isTappable` (the same fact, named for
// the question the markup asks).
test('offerabilityOf collapses availability plus every staleness signal to a binary', () => {
	assert.equal(offerabilityOf({ name: 'x', available: true }, false), 'offerable');
	assert.equal(
		offerabilityOf({ name: 'x', available: true }, true),
		'off',
		'account-wide stale resolves to off, not a third "stale" state'
	);
	assert.equal(
		offerabilityOf({ name: 'x', available: true, daemon_stale: true }, false),
		'off',
		"the row's own stale report resolves to off the same way"
	);
	assert.equal(
		offerabilityOf({ name: 'x' }, false),
		'off',
		'"we don\'t know" is off, not a third state'
	);
	assert.equal(offerabilityOf({ name: 'x', available: false }, false), 'off');
	// Locked ≠ dead: the tap is the probe that clears the mark.
	assert.equal(
		offerabilityOf({ name: 'x', available: false, availability: 'auth-error' }, false),
		'offerable'
	);
	assert.equal(
		offerabilityOf({ name: 'x', available: false, availability: 'auth-error' }, true),
		'off'
	);
});

test('offReasonOf gives a concrete reason only for verified-unavailable rows', () => {
	const shellNotFound = offReasonOf(
		{ name: 'codex', available: false, availability: 'shell-not-found' },
		false
	);
	assert.equal(shellNotFound.known, true);
	assert.equal(shellNotFound.text, 'not installed on this daemon');
	const authError = offReasonOf(
		{ name: 'codex', available: false, availability: 'auth-error' },
		false
	);
	assert.equal(authError.known, true);
	assert.equal(authError.text, 'auth failed — sign in again, then tap');
	// With the daemon's facts on the row: when, on which core, and whether
	// the Shell's own status verb agreed.
	const locked = offReasonOf(
		{
			name: 'claude-fable',
			available: false,
			availability: 'auth-error',
			auth_error: { since: '2026-09-09T03:36:30Z', seen_on: 'claude-sonnet', probe: 'signed-out' }
		},
		false
	);
	assert.equal(
		locked.text,
		'auth failed 03:36Z on claude-sonnet · the shell agrees: signed out — sign in again, then tap'
	);

	// Unverified: no invented specifics — the daemon never said why.
	const unverified = offReasonOf({ name: 'ghost' }, false);
	assert.equal(unverified.known, false);

	// Verified-available but the report is stale: never borrows the
	// unavailable copy — that would tell the reader a wrong reason.
	const stale = offReasonOf({ name: 'claude', available: true }, true);
	assert.equal(stale.known, false);
	assert.doesNotMatch(stale.text, /installed|auth/u);
});

test('grouping preserves cost-rank order within a shell and puts available shells first', () => {
	const profiles: RunnerProfile[] = [
		{ name: 'claude-haiku', shell: 'claude', available: true, cost_rank: 10 },
		{
			name: 'codex-mini',
			shell: 'codex',
			available: false,
			availability: 'shell-not-found',
			cost_rank: 20
		},
		{ name: 'claude', shell: 'claude', available: true, cost_rank: 30 },
		{
			name: 'codex',
			shell: 'codex',
			available: false,
			availability: 'shell-not-found',
			cost_rank: 25
		}
	];
	const groups = groupByShell(profiles);
	assert.deepEqual(
		groups.map((g) => g.shell),
		['claude', 'codex'],
		'the live shell sorts first even though codex-mini was reported second'
	);
	assert.equal(groups[0].allUnavailable, false);
	assert.deepEqual(
		groups[0].profiles.map((p) => p.name),
		['claude-haiku', 'claude'],
		'within a shell, incoming (cost_rank-ascending) order survives the grouping'
	);
	assert.equal(
		groups[1].allUnavailable,
		true,
		'every codex row is verified unavailable → the group collapses'
	);
});

test('a verified-unavailable core sorts to the end of its own shell, not its old cost-rank slot', () => {
	// Live shape from the account this shipped against: `codex-gpt-5.4-mini`
	// (retired, cost_rank 23) used to render between `codex-mini` (20) and
	// `codex` (25) — a dashed-border row sitting inline with runnable ones,
	// distinguished only by style, not position.
	const profiles: RunnerProfile[] = [
		{ name: 'codex-mini', shell: 'codex', available: true, cost_rank: 20 },
		{
			name: 'codex-gpt-5.4-mini',
			shell: 'codex',
			available: false,
			availability: 'retired',
			cost_rank: 23
		},
		{ name: 'codex', shell: 'codex', available: true, cost_rank: 25 },
		{ name: 'codex-full', shell: 'codex', available: true, cost_rank: 45 }
	];
	const [group] = groupByShell(profiles);
	assert.deepEqual(
		group.profiles.map((p) => p.name),
		['codex-mini', 'codex', 'codex-full', 'codex-gpt-5.4-mini'],
		'the retired core moves after every usable one, cost-rank order preserved within each bucket'
	);
	assert.equal(
		group.allUnavailable,
		false,
		'three of four cores are usable — the shell stays live'
	);
});

test('a shell with even one unverified row does not collapse — unverified is not the same claim as dead', () => {
	const profiles: RunnerProfile[] = [
		{ name: 'codex', shell: 'codex' }, // unverified: no `available` field at all
		{ name: 'codex-mini', shell: 'codex', available: false }
	];
	const [group] = groupByShell(profiles);
	assert.equal(
		group.allUnavailable,
		false,
		'one unknown row is not the same claim as "every row is dead"'
	);
});

test('deadShellReason names the reason a shell tab is off', () => {
	const [one] = groupByShell([
		{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' }
	]);
	assert.equal(deadShellReason(one), 'not installed on this daemon');
});

// His mid-flight steer, verbatim: "a very dumb but already good improvement
// would be to add a separate shell selector which renders available cores
// for it below" — `defaultShell` is which tab a fresh rack opens on.
test('defaultShell opens on whoever answers the next wake, never a dead tab by default', () => {
	const groups = groupByShell([
		{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' },
		{ name: 'claude-haiku', shell: 'claude', available: true },
		{ name: 'claude-sonnet', shell: 'claude', available: true, selected: true }
	]);
	assert.equal(defaultShell(groups, 'claude-sonnet'), 'claude');
	assert.equal(
		defaultShell(groups, null),
		'claude',
		'no resolved next-wake profile falls back to the first live shell'
	);
	const allDead = groupByShell([
		{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' }
	]);
	assert.equal(
		defaultShell(allDead, null),
		'codex',
		'no live shell at all falls back to whatever sorts first'
	);
});

test('orderRows: shell default first, then economy · balanced · strong · unclassed; cost only inside a tier', () => {
	const rows: RunnerProfile[] = [
		{ name: 'codex-gpt-6-astra', shell: 'codex', model: 'gpt-6-astra', cost_rank: 1 },
		{ name: 'codex-full', shell: 'codex', model: 'gpt-5.6-sol', class: 'strong', cost_rank: 45 },
		{
			name: 'codex-terra',
			shell: 'codex',
			model: 'gpt-5.6-terra',
			class: 'balanced',
			cost_rank: 30
		},
		{ name: 'codex', shell: 'codex', class: 'balanced', cost_rank: 25 },
		{ name: 'codex-mini', shell: 'codex', model: 'gpt-5.6-luna', class: 'economy', cost_rank: 20 }
	];
	assert.deepEqual(
		orderRows(rows).map((row) => row.name),
		['codex', 'codex-mini', 'codex-terra', 'codex-full', 'codex-gpt-6-astra']
	);
	assert.equal(headline(rows[3]), 'codex');
	assert.equal(headline(rows[0]), 'gpt-6-astra');
	assert.equal(
		headline({
			name: 'claude-fable',
			shell: 'claude',
			model: 'fable',
			observed_model: 'claude-fable-5-1'
		}),
		'claude-fable-5-1'
	);
	assert.equal(tierLabel(rows[0]), 'unclassed');
});
