import assert from 'node:assert/strict';
import test from 'node:test';
import type { LiveRun } from '../liveRuns.ts';
import type { RunnerProfile } from '../runners.ts';
import { dispatcherRun, handsFor, nextProfile, shellRows } from './sketchA.ts';

function run(overrides: Partial<LiveRun>): LiveRun {
	return {
		id: 'id',
		kind: 'daemon',
		stream: 'thread',
		label: '',
		name: 'run',
		run_id: 'run',
		repo_label: 'org/repo',
		started_at: '2026-08-20T19:00:00Z',
		last_seen: '2026-08-20T19:01:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { shell: 'claude', core: 'opus' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		...overrides
	};
}

test('dispatcher excludes hands and groups them beneath their parent', () => {
	const parent = run({ run_id: 'parent' });
	const hand = run({ run_id: 'hand', is_subspawn: true, parent_run_id: 'parent' });
	assert.equal(dispatcherRun([hand, parent])?.run_id, 'parent');
	assert.deepEqual(
		handsFor([hand, parent], parent).map((row) => row.run_id),
		['hand']
	);
});

test('next profile follows request, sticky, then default', () => {
	const profiles = ['default', 'sticky', 'request'].map((name) => ({ name }) as RunnerProfile);
	const base = { generated_at: '', reported_at: null, stale: false, default: 'default', profiles };
	assert.equal(
		nextProfile({ ...base, wake_request: null, sticky: { profile: 'sticky' } })?.name,
		'sticky'
	);
	assert.equal(
		nextProfile({
			...base,
			wake_request: {
				request_id: '1',
				profile: 'request',
				repo_label: null,
				environment: null,
				requested_at: null,
				status: 'pending'
			}
		})?.name,
		'request'
	);
});

test('shell in use leads, then rows follow recent use and catalog order', () => {
	const profiles = [
		{ name: 'codex', shell: 'codex' },
		{ name: 'claude', shell: 'claude' },
		{ name: 'other', shell: 'other' }
	] as RunnerProfile[];
	const current = run({ runner: { shell: 'claude' } });
	const recent = run({
		run_id: 'recent',
		runner: { shell: 'other' },
		last_seen: '2026-08-20T19:20:00Z'
	});
	assert.deepEqual(
		shellRows(profiles, [], [current, recent], current).map((row) => row.shell),
		['claude', 'other', 'codex']
	);
});
