import assert from 'node:assert/strict';
import test from 'node:test';
import { dispatcherRun, handsFor, shellBays } from './garage/garage.ts';

const base = {
	kind: 'run',
	stream: '',
	label: '',
	repo_label: 'repo',
	started_at: null,
	last_seen: null,
	runner: {},
	phase: null,
	card_text: null,
	card_updated_at: null,
	daemon_stale: false
};
const parent = {
	...base,
	id: 'p',
	name: 'parent',
	run_id: 'parent',
	parent_run_id: null,
	is_subspawn: false,
	last_seen: '2026-01-01T00:00:00Z',
	runner: { shell: 'claude', core: 'opus' }
};
const hand = {
	...base,
	id: 'h',
	name: 'hand',
	run_id: 'hand',
	parent_run_id: 'parent',
	is_subspawn: true,
	last_seen: '2026-01-02T00:00:00Z',
	runner: { shell: 'codex', core: 'sol' }
};

test('a strand is never the dispatcher and remains attached as a hand', () => {
	assert.equal(dispatcherRun([parent, hand])?.run_id, 'parent');
	assert.deepEqual(
		handsFor(parent, [parent, hand]).map((run) => run.run_id),
		['hand']
	);
});

test('NOW shell sorts first, then recent use, then catalog order', () => {
	const runners = {
		generated_at: '',
		reported_at: null,
		stale: false,
		default: 'claude-opus',
		wake_request: null,
		profiles: [
			{ name: 'claude-opus', shell: 'claude', model: 'opus' },
			{ name: 'codex-sol', shell: 'codex', model: 'sol' },
			{ name: 'other-core', shell: 'other', model: 'core' }
		]
	};
	assert.deepEqual(
		shellBays(runners, [], [parent, hand], parent).map((bay) => bay.shell),
		['claude', 'codex', 'other']
	);
});
