import assert from 'node:assert/strict';
import test from 'node:test';

import { garageHands, garageNow, garageShells, nextWake } from './garageC.ts';
import type { LiveRun } from '../liveRuns.ts';
import type { RunnersResponse } from '../runners.ts';

const base = {
	kind: 'daemon',
	stream: 'thread',
	label: '',
	name: '',
	repo_label: 'org/repo',
	phase: 'running',
	card_text: null,
	card_updated_at: null,
	relics_counts: null,
	mood: null,
	mood_glyph: null,
	mood_frames: null,
	mood_rest: null,
	mood_pitch: null,
	topics: null,
	stop_requested: false,
	daemon_stale: false
} as const;

function run(id: string, values: Partial<LiveRun> = {}): LiveRun {
	return {
		...base,
		id,
		run_id: id,
		started_at: '2026-08-20T19:00:00Z',
		last_seen: '2026-08-20T19:00:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { shell: 'claude', core: 'opus' },
		...values
	};
}

test('NOW excludes strands and hands stay attached to their dispatcher', () => {
	const parent = run('parent');
	const hand = run('hand', {
		is_subspawn: true,
		parent_run_id: 'parent',
		last_seen: '2026-08-20T20:00:00Z'
	});
	assert.equal(garageNow([parent, hand])?.run_id, 'parent');
	assert.deepEqual(
		garageHands([parent, hand], parent).map((row) => row.run_id),
		['hand']
	);
});

test('shell in use wins, then recent use, then catalog order', () => {
	const runners = {
		default: 'claude',
		wake_request: null,
		sticky: null,
		profiles: [
			{ name: 'codex', shell: 'codex', model: 'default' },
			{ name: 'claude', shell: 'claude', model: 'opus' },
			{ name: 'local', shell: 'local', model: 'tiny' }
		]
	} as RunnersResponse;
	const rows = garageShells(
		runners,
		[],
		[run('old'), run('new', { runner: { shell: 'codex' }, last_seen: '2026-08-20T20:00:00Z' })],
		run('now')
	);
	assert.deepEqual(
		rows.map((row) => row.shell),
		['claude', 'codex', 'local']
	);
});

test('the earliest valid scheduled wake wins', () => {
	const wake = (id: string, scheduled_for: string | null) => ({ id, scheduled_for }) as never;
	assert.equal(
		nextWake([wake('late', '2026-08-20T22:00:00Z'), wake('soon', '2026-08-20T21:00:00Z')])?.id,
		'soon'
	);
});
