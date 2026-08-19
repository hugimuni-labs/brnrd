import assert from 'node:assert/strict';
import test from 'node:test';

import { availabilityOf, collapsedShellSummary, groupByShell, isTappable } from './spoolRack.ts';
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

test('the collapsed summary names the reason and the core count, singular and plural', () => {
	const [one] = groupByShell([
		{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' }
	]);
	assert.equal(collapsedShellSummary(one), 'codex — not installed on this daemon · 1 core');

	const [many] = groupByShell([
		{ name: 'codex', shell: 'codex', available: false, availability: 'shell-not-found' },
		{ name: 'codex-mini', shell: 'codex', available: false, availability: 'shell-not-found' }
	]);
	assert.equal(collapsedShellSummary(many), 'codex — not installed on this daemon · 2 cores');
});
