import assert from 'node:assert/strict';
import test from 'node:test';
import { findLiveRunForRoute } from '$lib/runNode';
import type { LiveRun, LiveRunsResponse } from '$lib/liveRuns';
import type { SurfaceResponse, SurfaceFile } from '$lib/surface';

// Test fixtures for the live route implementation (issue #2162)

// Representative API fixture: live run with card that should match a route
const LIVE_RUN_FIXTURE: LiveRun = {
	id: 'run-1',
	kind: 'strand',
	stream: 'evt-1790914965682473000-5tr4',
	label: 'The loom implementation',
	name: 'live-route-test',
	run_id: 'run-261002-0423-tjju',
	repo_label: 'hugimuni-labs/brnrd',
	started_at: '2026-10-02T04:22:45Z',
	last_seen: '2026-10-02T04:37:45Z',
	parent_run_id: null,
	is_subspawn: false,
	runner: {
		name: 'vibe',
		shell: 'vibe',
		core: 'default',
		class: 'economy',
		model_observed: 'mistral-medium-3.5'
	},
	phase: 'running',
	card_text: '## Now\nImplementing live route for issue #2162\n\n## Next\n- [x] Add live feed fetching\n- [ ] Capture screenshots',
	course: { done: 1, total: 2, current: 'Capture screenshots' },
	card_updated_at: '2026-10-02T04:37:45Z',
	relics_counts: { commit: 0, kb: 0, pr: 0 },
	relics_kb_pages: [],
	mood: 'focused',
	mood_glyph: '🎯',
	mood_frames: [['🎯'], ['🎯']],
	mood_rest: '🎯',
	mood_pitch: 1.0,
	topics: ['the-loom'],
	lifecycle: 'weaving',
	status: null,
	daemon_stale: false
};

// Successive card version (same run, updated card)
const LIVE_RUN_FIXTURE_UPDATED: LiveRun = {
	...LIVE_RUN_FIXTURE,
	card_text: '## Now\nImplementing live route for issue #2162\n\n## Next\n- [x] Add live feed fetching\n- [x] Capture screenshots\n- [ ] Update report',
	course: { done: 2, total: 3, current: 'Update report' },
	card_updated_at: '2026-10-02T04:42:45Z'
};

// Live run that has closed (no longer in live feed)
const LIVE_RUN_CLOSED: LiveRun = {
	...LIVE_RUN_FIXTURE,
	lifecycle: 'closing',
	phase: 'finalizing'
};

// Same run id but different repo (should not match)
const LIVE_RUN_DIFFERENT_REPO: LiveRun = {
	...LIVE_RUN_FIXTURE,
	repo_label: 'hugimuni-labs/other-repo',
	run_id: 'run-261002-0423-tjju'
};

// Withheld response fixture
const LIVE_RUNS_WITHHELD: LiveRunsResponse = {
	generated_at: '2026-10-02T04:37:45Z',
	runs: [],
	stale: false,
	reported_at: '2026-10-02T04:37:45Z',
	spawn_max_concurrent: 10,
	withheld: {
		kind: 'live_runs',
		reason: 'not_authed',
		detail: 'Session expired for live feed access'
	}
};

// Auth error response
const LIVE_RUNS_AUTH_ERROR: LiveRunsResponse = {
	generated_at: '2026-10-02T04:37:45Z',
	runs: [],
	stale: false,
	reported_at: '2026-10-02T04:37:45Z',
	spawn_max_concurrent: null,
	withheld: null
};

// Error response
const LIVE_RUNS_ERROR: LiveRunsResponse = {
	generated_at: '2026-10-02T04:37:45Z',
	runs: [],
	stale: true,
	reported_at: null,
	spawn_max_concurrent: null,
	withheld: null
};

test('Live route matching: exact match with sanitized slugs', () => {
	const runs = [LIVE_RUN_FIXTURE];
	const matched = findLiveRunForRoute(runs, 'hugimuni-labs__brnrd', 'run-261002-0423-tjju');
	
	assert.notEqual(matched, null);
	assert.equal(matched?.run_id, LIVE_RUN_FIXTURE.run_id);
	assert.equal(matched?.repo_label, LIVE_RUN_FIXTURE.repo_label);
});

test('Live route matching: same run id different repo does not match', () => {
	const runs = [LIVE_RUN_FIXTURE, LIVE_RUN_DIFFERENT_REPO];
	const matched = findLiveRunForRoute(runs, 'hugimuni-labs__brnrd', 'run-261002-0423-tjju');
	
	assert.notEqual(matched, null);
	assert.equal(matched?.run_id, LIVE_RUN_FIXTURE.run_id);
	assert.equal(matched?.repo_label, 'hugimuni-labs/brnrd');
});

test('Live route matching: successive card versions', () => {
	const runs = [LIVE_RUN_FIXTURE_UPDATED];
	const matched = findLiveRunForRoute(runs, 'hugimuni-labs__brnrd', 'run-261002-0423-tjju');
	
	assert.notEqual(matched, null);
	assert.equal(matched?.card_text, LIVE_RUN_FIXTURE_UPDATED.card_text);
});

test('Live route matching: live to closed transition', () => {
	const runs: LiveRun[] = []; // Closed run would not appear in live feed
	const matched = findLiveRunForRoute(runs, 'hugimuni-labs__brnrd', 'run-261002-0423-tjju');
	
	assert.equal(matched, null); // No match when run has closed and is no longer in live feed
});

test('Live route matching: no runs returns null', () => {
	const matched = findLiveRunForRoute([], 'hugimuni-labs__brnrd', 'run-261002-0423-tjju');
	assert.equal(matched, null);
});

// Test fixture validation
test('Live run fixture carries required fields for UI rendering', () => {
	assert.ok(LIVE_RUN_FIXTURE.name, 'Live run should have a name');
	assert.ok(LIVE_RUN_FIXTURE.repo_label, 'Live run should have a repo_label');
	assert.ok(LIVE_RUN_FIXTURE.run_id, 'Live run should have a run_id');
	assert.ok(LIVE_RUN_FIXTURE.card_text, 'Live run should have card_text for UI');
	assert.ok(LIVE_RUN_FIXTURE.phase, 'Live run should have phase for UI');
	assert.ok(LIVE_RUN_FIXTURE.started_at, 'Live run should have started_at for UI');
	assert.ok(LIVE_RUN_FIXTURE.last_seen, 'Live run should have last_seen for UI');
});

test('Live run fixture has valid card text structure', () => {
	const cardText = LIVE_RUN_FIXTURE.card_text;
	assert.ok(cardText?.includes('## Now'), 'Card should contain ## Now section');
	assert.ok(cardText?.includes('issue #2162'), 'Card should reference the issue');
});

test('Updated live run fixture shows progress', () => {
	const originalCard = LIVE_RUN_FIXTURE.card_text;
	const updatedCard = LIVE_RUN_FIXTURE_UPDATED.card_text;
	
	assert.notEqual(originalCard, updatedCard);
	assert.ok(updatedCard?.includes('[x] Capture screenshots'), 'Updated card should show completed task');
});