// A deterministic replay of one real-shaped day in the room, for `/new?demo`:
// wake → boundaries → spawn → message → inject → returns → closeout. Every
// frame is a full live-runs snapshot; the page steps through them on a fixed
// cadence, so the causal choreography can be watched (and screenshotted)
// without a live daemon. The shapes mirror `fetchLiveRuns`'s wire exactly —
// this file is fixture, not vocabulary: nothing here invents semantics.

import type { LiveRun } from '$lib/liveRuns';

function liveRun(over: Partial<LiveRun> & { run_id: string }): LiveRun {
	return {
		id: over.run_id,
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
		label: '',
		name: '',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: '2026-08-26T10:00:00Z',
		last_seen: '2026-08-26T10:20:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { name: 'claude-fable', shell: 'claude', core: 'fable', class: 'strong' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		relics_counts: null,
		mood: null,
		topics: [],
		stop_requested: false,
		lifecycle: null,
		await_until: null,
		room: null,
		edge: null,
		portals: { pending: 0, oldest_at: null },
		daemon_stale: false,
		...over
	} as LiveRun;
}

function edge(act: string, detail: string, at: string, injected = false) {
	return {
		at,
		phase: 'PostToolUse',
		act,
		tools: ['Bash'],
		detail,
		out_bytes: 412,
		injected,
		dir: '.'
	};
}

const resident = (over: Partial<LiveRun>) =>
	liveRun({
		run_id: 'run-260826-1049-f67f',
		name: 'the-axonometric-room',
		// The face, as the daemon would resolve it against `brr.emotes` —
		// fixture stands in for the wire, same shape (`primed`: repro in
		// hand, coffee metaphorically hot).
		mood: 'primed',
		mood_glyph: 'b·_·d',
		mood_rest: 'b·_·d',
		mood_frames: [['b·_·d', 'bo_od', 'b·_·d']],
		mood_pitch: 0.55,
		card_text: '## Plan\n- [x] orient\n- [x] geometry\n- [ ] the room\n- [ ] drive\n- [ ] PR',
		room: { env: 'host', branch: 'brr/the-operational-diorama', dir: null },
		...over
	});

const strandA = (over: Partial<LiveRun> = {}) =>
	liveRun({
		run_id: 'run-260826-1102-s0na',
		name: 'the-lane-that-earns-its-cable',
		parent_run_id: 'run-260826-1049-f67f',
		is_subspawn: true,
		started_at: '2026-08-26T11:02:00Z',
		runner: { name: 'claude-sonnet', shell: 'claude', core: 'sonnet', class: 'balanced' },
		room: { env: 'worktree', branch: 'brr/the-lane', dir: 'brr-wt-s0na' },
		edge: edge('probe', 'git status --short', '2026-08-26T11:02:30Z'),
		...over
	});

const strandB = (over: Partial<LiveRun> = {}) =>
	liveRun({
		run_id: 'run-260826-1107-h4ik',
		name: 'the-quiet-vigil',
		parent_run_id: 'run-260826-1049-f67f',
		is_subspawn: true,
		started_at: '2026-08-26T11:07:00Z',
		runner: { name: 'claude-haiku', shell: 'claude', core: 'haiku', class: 'economy' },
		lifecycle: 'awaiting',
		await_until: '2026-08-26T11:40:00Z',
		room: { env: 'worktree', branch: 'brr/the-vigil', dir: 'brr-wt-h4ik' },
		edge: edge('wait', 'brnrd await --timeout 30m', '2026-08-26T11:07:40Z'),
		...over
	});

/** The replay, in order. Frame cadence belongs to the page. */
export function demoFrames(): LiveRun[][] {
	return [
		// wake — the resident alone, orienting
		[resident({ edge: edge('orient', 'Read design-resident-field.md', '2026-08-26T10:49:20Z') })],
		// a mutate boundary
		[resident({ edge: edge('mutate', 'Edit isoField.ts', '2026-08-26T10:52:00Z') })],
		// dispatch — a strand rises on the lane
		[
			resident({
				edge: edge('dispatch', 'spawn: the-lane-that-earns-its-cable', '2026-08-26T11:02:00Z')
			}),
			strandA()
		],
		// both working
		[
			resident({ edge: edge('probe', 'node --test isoField.test.ts', '2026-08-26T11:04:00Z') }),
			strandA({ edge: edge('mutate', 'Edit +page.svelte', '2026-08-26T11:04:10Z') })
		],
		// a second strand, economy core, holding a vigil
		[
			resident({ edge: edge('dispatch', 'spawn: the-quiet-vigil', '2026-08-26T11:07:00Z') }),
			strandA({ edge: edge('publish', 'git push origin brr/the-lane', '2026-08-26T11:06:50Z') }),
			strandB()
		],
		// correspondence arrives — ◈ rests at the gate
		[
			resident({
				edge: edge('probe', 'npm run lint', '2026-08-26T11:09:00Z'),
				portals: { pending: 1, oldest_at: '2026-08-26T11:09:10Z' }
			}),
			strandA(),
			strandB()
		],
		// the world folds in — the read is attested
		[
			resident({ edge: edge('orient', 'brnrd do', '2026-08-26T11:10:00Z', true) }),
			strandA(),
			strandB()
		],
		// the first strand returns home
		[resident({ edge: edge('probe', 'gh pr checks 1636', '2026-08-26T11:14:00Z') }), strandB()],
		// the vigil resolves and returns; the resident closes out
		[
			resident({
				lifecycle: 'closing',
				edge: edge('publish', 'git push · PR opened', '2026-08-26T11:18:00Z')
			})
		],
		// between wakes — the floor holds
		[]
	];
}
