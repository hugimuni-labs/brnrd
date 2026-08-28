// The reference trace (#1652 §Reference trace for the first proof): eight
// boundaries, fixture-shaped exactly like `fetchLiveRuns`'s wire, that a
// camera must be able to tell as a journey without opening a dossier:
//
//   #81 orient   src/frontend
//   #82 mutate   src/frontend/src/lib · asciiRoom.ts
//   #83 probe    src/frontend/tests
//   #84 dispatch brnrd-knowledge/design
//   #85 probe    src/frontend/tests + injected evt-B
//   #86 mutate   .card / chart table
//   #87 publish  forge / PR
//   #88 cut      live weave → immutable Cloth
//
// Used by the `/ascii?demo` replay and by the acceptance tests — one
// fixture, every camera, same story.

import type { LiveRun } from './liveRuns.ts';

function liveRun(over: Partial<LiveRun> & { run_id: string }): LiveRun {
	return {
		id: over.run_id,
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
		label: '',
		name: '',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: '2026-08-27T10:00:00Z',
		last_seen: '2026-08-27T10:20:00Z',
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

function edge(
	act: string,
	detail: string,
	at: string,
	dir = '.',
	injected = false
): NonNullable<LiveRun['edge']> {
	return {
		at,
		phase: 'PostToolUse',
		act,
		tools: ['Bash'],
		detail,
		out_bytes: 412,
		injected,
		dir
	};
}

const resident = (over: Partial<LiveRun>) =>
	liveRun({
		run_id: 'run-260827-1000-ref1',
		name: 'the-reference-journey',
		mood_rest: 'b·_·d',
		card_text: '## Plan\n- [x] orient\n- [ ] the change\n- [ ] prove it\n- [ ] publish',
		room: { env: 'host', branch: 'brr/the-reference-journey', dir: null },
		...over
	});

const strand = (over: Partial<LiveRun> = {}) =>
	liveRun({
		run_id: 'run-260827-1012-des1',
		name: 'the-design-sweep',
		parent_run_id: 'run-260827-1000-ref1',
		is_subspawn: true,
		started_at: '2026-08-27T10:12:00Z',
		repo_label: 'hugimuni-labs/brnrd-knowledge',
		runner: { name: 'claude-sonnet', shell: 'claude', core: 'sonnet', class: 'balanced' },
		room: { env: 'worktree', branch: 'brr/the-design-sweep', dir: 'brr-wt-des1' },
		edge: edge(
			'orient',
			'Read design-room-operational-topology.md',
			'2026-08-27T10:12:30Z',
			'design'
		),
		...over
	});

/** The eight numbered frames. Frame cadence belongs to the caller. */
export function referenceFrames(): LiveRun[][] {
	return [
		// #81 orient — the resident wakes inside the tree
		[
			resident({
				edge: edge('orient', 'Read asciiRoom.ts', '2026-08-27T10:02:00Z', 'src/frontend')
			})
		],
		// #82 mutate — walks deeper through shared-prefix chambers to edit
		[
			resident({
				edge: edge('mutate', 'Edit asciiRoom.ts', '2026-08-27T10:05:00Z', 'src/frontend/src/lib')
			})
		],
		// #83 probe — back through the tree to the local rig
		[
			resident({
				edge: edge(
					'probe',
					'node --test asciiRoom.test.ts',
					'2026-08-27T10:08:00Z',
					'src/frontend/tests'
				)
			})
		],
		// #84 dispatch — to its bay while a strand crosses to the knowledge island
		[
			resident({
				edge: edge('dispatch', 'spawn: the-design-sweep', '2026-08-27T10:12:00Z'),
				// The next boundary injects this waiting letter. Keeping the pending
				// state in the preceding frame makes the gate marker observable.
				portals: { pending: 1, oldest_at: '2026-08-27T10:12:30Z' }
			}),
			strand()
		],
		// #85 probe + injected evt-B — a letter reaches the resident at tests
		// without moving the resident.
		//
		// `crossings` carries the same boundary the `edge` above marks
		// injected. The wire publishes both (brnrd#1679) because `edge` is a
		// cursor and `crossings` is the stream; the replay has to publish both
		// too, or the ceremony would have nothing attested to ride and the
		// demo lane would silently exercise none of it.
		[
			resident({
				edge: edge(
					'probe',
					'node --test asciiRoom.test.ts',
					'2026-08-27T10:14:00Z',
					'src/frontend/tests',
					true
				),
				crossings: [
					edge(
						'probe',
						'node --test asciiRoom.test.ts',
						'2026-08-27T10:14:00Z',
						'src/frontend/tests',
						true
					)
				]
			}),
			strand()
		],
		// #86 mutate .card — the chart, because it actually edits control state
		[
			resident({
				edge: edge('mutate', 'Write .card', '2026-08-27T10:16:00Z'),
				relics_counts: { commit: 1 }
			}),
			strand()
		],
		// #87 publish — the forge; durable produce
		[
			resident({
				edge: edge(
					'publish',
					'git push origin brr/the-reference-journey · PR opened',
					'2026-08-27T10:18:00Z'
				),
				relics_counts: { commit: 2, pr: 1 }
			})
		],
		// #88 cut — the body disappears; the journey remains in Cloth
		[
			resident({
				lifecycle: 'closing',
				edge: edge('publish', 'brnrd cut bolt.md', '2026-08-27T10:20:00Z'),
				relics_counts: { commit: 2, pr: 1 }
			})
		]
	];
}
