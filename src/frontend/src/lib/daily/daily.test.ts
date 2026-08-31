import assert from 'node:assert/strict';
import test from 'node:test';
import type { LiveRun } from '../liveRuns.ts';
import { MAP_ROW_BOUNDS, dailyLiveBars, mapRows } from './daily.ts';

// The buoy / island / reef / hash-deep-link tests that stood here went with
// the composition they covered (2026-08-31): `/daily` wears the main
// dashboard now, so there is no second telling of the warp, the branch
// terrain, or the kb count to assert about. What is left is the two things
// the live-runs view on that route still computes for itself.

const run = (id: string, parent: string | null = null): LiveRun =>
	({
		id,
		run_id: id,
		parent_run_id: parent,
		is_subspawn: parent !== null,
		name: id,
		label: '',
		stream: '',
		kind: 'daemon',
		repo_label: 'org/repo',
		started_at: null,
		last_seen: null,
		runner: {},
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		course: { done: 1, total: 3, current: 'build' },
		portals: { pending: 2, oldest_at: null },
		room: { env: 'worktree', branch: `brr/${id}`, dir: id }
	}) as LiveRun;

test('daily live bars nest strands beneath their live parent', () => {
	const bars = dailyLiveBars([run('child', 'parent'), run('parent')]);
	assert.deepEqual(
		bars.map((bar) => [bar.name, bar.depth, bar.course, bar.pending]),
		[
			['parent', 0, '1/3', 2],
			['child', 1, '1/3', 2]
		]
	);
});

test('the inline scene takes a share of the viewport, not a constant row count', () => {
	// A phone (~740px of usable height) and a desktop must not get the same
	// map: 22 rows is most of the former, which is what pushed every section
	// below the field off the first `/daily`.
	const phone = mapRows('inline', 740);
	const desktop = mapRows('inline', 1080);
	assert.ok(phone < desktop, `expected the phone scene to be shorter (${phone} vs ${desktop})`);
	assert.equal(phone, Math.round((740 * MAP_ROW_BOUNDS.inline.share) / 16.2));
});

test('the inline scene stays inside its own bounds at both extremes', () => {
	assert.equal(mapRows('inline', 200), MAP_ROW_BOUNDS.inline.min);
	assert.equal(mapRows('inline', 6000), MAP_ROW_BOUNDS.inline.max);
});

test('the expanded stage is always taller than the glance it replaces', () => {
	for (const height of [640, 740, 900, 1080, 1440]) {
		assert.ok(
			mapRows('full', height) > mapRows('inline', height),
			`the stage must gain rows at ${height}px`
		);
	}
});

test('an unmeasured viewport floors to a small map, never an empty frame', () => {
	// SSR and the first client frame both report 0. Zero rows renders as a
	// blank bordered box, which reads as broken; a short map reads as a map.
	assert.equal(mapRows('inline', 0), MAP_ROW_BOUNDS.inline.min);
	assert.equal(mapRows('full', 0), MAP_ROW_BOUNDS.full.min);
	assert.equal(mapRows('inline', Number.NaN), MAP_ROW_BOUNDS.inline.min);
	assert.equal(mapRows('inline', 740, 0), MAP_ROW_BOUNDS.inline.min);
});
