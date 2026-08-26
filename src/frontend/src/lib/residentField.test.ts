// The field's pure derivations: topology from a snapshot, receipts from a
// diff. The drawing component leans on these being exactly right — a wrong
// root promotes a strand to a fake resident; a wrong diff animates an event
// that never happened, which is the one sin the motion doctrine names
// (design-resident-field.md §Causal motion: "Motion is a receipt, never
// ambient liveliness").
import { test } from 'node:test';
import { deepEqual, equal, ok } from 'node:assert/strict';

import {
	ACT_COLORS,
	actColor,
	benchCommand,
	buildField,
	diffFieldEvents,
	edgeParts,
	fieldRunKey,
	truncPathTail
} from './residentField.ts';
import type { LiveRun } from './liveRuns';

function run(over: Partial<LiveRun> & { id: string }): LiveRun {
	return {
		kind: 'daemon',
		stream: null,
		label: null,
		name: null,
		run_id: over.id,
		repo_label: 'org/repo',
		started_at: '2026-08-25T20:00:00Z',
		last_seen: '2026-08-25T20:01:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: null,
		phase: 'working',
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
		...over
	} as LiveRun;
}

test('a resident with two strands is one root with two limbs, oldest first', () => {
	const field = buildField([
		run({ id: 'r1', started_at: '2026-08-25T20:00:00Z' }),
		run({
			id: 's2',
			is_subspawn: true,
			parent_run_id: 'r1',
			started_at: '2026-08-25T20:10:00Z'
		}),
		run({
			id: 's1',
			is_subspawn: true,
			parent_run_id: 'r1',
			started_at: '2026-08-25T20:05:00Z'
		})
	]);
	equal(field.length, 1);
	equal(fieldRunKey(field[0].run), 'r1');
	equal(field[0].orphan, false);
	deepEqual(
		field[0].limbs.map((l) => fieldRunKey(l.run)),
		['s1', 's2']
	);
});

test('a nested strand collapses into its first-level limb as a hand count', () => {
	const field = buildField([
		run({ id: 'r1' }),
		run({ id: 's1', is_subspawn: true, parent_run_id: 'r1' }),
		run({ id: 'g1', is_subspawn: true, parent_run_id: 's1' }),
		run({ id: 'g2', is_subspawn: true, parent_run_id: 's1' })
	]);
	equal(field.length, 1);
	equal(field[0].limbs.length, 1);
	equal(field[0].limbs[0].hands, 2);
});

test('a strand whose dispatcher left the board roots itself, marked orphan', () => {
	const field = buildField([
		run({ id: 's1', is_subspawn: true, parent_run_id: 'gone', started_at: '2026-08-25T21:00:00Z' }),
		run({ id: 'r1', started_at: '2026-08-25T20:00:00Z' })
	]);
	deepEqual(
		field.map((r) => [fieldRunKey(r.run), r.orphan]),
		[
			['r1', false],
			['s1', true]
		]
	);
});

test('the mount produces no events — nothing happened, nothing moves', () => {
	deepEqual(diffFieldEvents(null, [run({ id: 'r1' })]), []);
});

test('an unchanged snapshot produces no events', () => {
	const snap = [
		run({
			id: 'r1',
			edge: {
				at: 't1',
				phase: null,
				act: 'probe',
				tools: [],
				detail: 'ls',
				out_bytes: 3,
				injected: false
			}
		})
	];
	deepEqual(diffFieldEvents(snap, snap), []);
});

test('appearing, boundary-crossing, injected, and departing runs each yield their receipt', () => {
	const before = [
		run({
			id: 'r1',
			edge: {
				at: 't1',
				phase: null,
				act: 'probe',
				tools: [],
				detail: 'ls',
				out_bytes: 3,
				injected: false
			}
		}),
		run({ id: 's-old', is_subspawn: true, parent_run_id: 'r1' })
	];
	const after = [
		run({
			id: 'r1',
			edge: {
				at: 't2',
				phase: null,
				act: 'mutate',
				tools: [],
				detail: 'edit',
				out_bytes: 9,
				injected: true
			}
		}),
		run({ id: 's-new', is_subspawn: true, parent_run_id: 'r1' })
	];
	const events = diffFieldEvents(before, after);
	deepEqual(events, [
		{ kind: 'inject', runId: 'r1', parentId: null },
		{ kind: 'spawn', runId: 's-new', parentId: 'r1' },
		{ kind: 'return', runId: 's-old', parentId: 'r1' }
	]);
});

test('a boundary without injection is local, never an arrival', () => {
	const before = [
		run({
			id: 'r1',
			edge: {
				at: 't1',
				phase: null,
				act: 'probe',
				tools: [],
				detail: 'ls',
				out_bytes: 3,
				injected: true
			}
		})
	];
	const after = [
		run({
			id: 'r1',
			edge: {
				at: 't2',
				phase: null,
				act: 'probe',
				tools: [],
				detail: 'cat',
				out_bytes: 5,
				injected: false
			}
		})
	];
	deepEqual(diffFieldEvents(before, after), [{ kind: 'boundary', runId: 'r1', parentId: null }]);
});

test('correspondence arriving at the door is a message receipt; draining is a read', () => {
	const before = [run({ id: 'r1', portals: { pending: 0, oldest_at: null } })];
	const arrived = [run({ id: 'r1', portals: { pending: 1, oldest_at: '2026-08-25T22:05:00Z' } })];
	deepEqual(diffFieldEvents(before, arrived), [{ kind: 'message', runId: 'r1', parentId: null }]);
	// The queue draining used to move no packet, on the theory that the
	// inject boundary was the read's own receipt — measured false on the
	// live room (2026-08-26): the resting markers just vanished. A drain
	// is a `read` event now, so the marker can be carried home.
	deepEqual(diffFieldEvents(arrived, before), [{ kind: 'read', runId: 'r1', parentId: null }]);
	// A daemon that never attested a portal cannot produce arrivals.
	deepEqual(diffFieldEvents([run({ id: 'r1' })], [run({ id: 'r1' })]), []);
});

test('the act palette mirrors the console and unknown acts recede', () => {
	// Hand-mirrored across the Python/TS seam (operator_console/tui.py,
	// #1623) — this pin is the tripwire for one side repainting alone.
	deepEqual(Object.keys(ACT_COLORS).sort(), [
		'dispatch',
		'mutate',
		'orient',
		'probe',
		'publish',
		'wait'
	]);
	equal(actColor('mutate'), '#d3a75e');
	equal(actColor('never-heard-of-it'), actColor(null));
});

test('edgeParts splits act from detail and colors the act', () => {
	const parts = edgeParts({
		at: 't1',
		phase: null,
		act: 'publish',
		tools: [],
		detail: 'git push',
		out_bytes: 12,
		injected: false
	});
	ok(parts);
	equal(parts.act, 'publish');
	equal(parts.detail, 'git push');
	equal(parts.color, ACT_COLORS.publish);
	equal(edgeParts(null), null);
	equal(
		edgeParts({
			at: 't',
			phase: null,
			act: null,
			tools: [],
			detail: null,
			out_bytes: null,
			injected: false
		}),
		null
	);
});

test('pending falling is a read — the resting marker travels, never vanishes', () => {
	const before = [run({ id: 'r1', portals: { pending: 2, oldest_at: '2026-08-26T11:00:00Z' } })];
	const after = [run({ id: 'r1', portals: { pending: 0, oldest_at: null } })];
	const events = diffFieldEvents(before, after);
	deepEqual(events, [{ kind: 'read', runId: 'r1', parentId: null }]);
	// And unchanged pending emits nothing — a still door is a still field.
	deepEqual(diffFieldEvents(after, after), []);
});

test('the bench strips env scaffolding and keeps path tails', () => {
	equal(
		benchCommand('OUT=/Users/g/.brr/outbox/evt-1 cat > "$OUT/.card" <<EOF'),
		'cat > "$OUT/.card" <<EOF'
	);
	equal(benchCommand('A=1 B="x y" git status'), 'git status');
	equal(benchCommand('git push origin main'), 'git push origin main');
	// A detail that is ONLY assignments still shows something, never blank.
	equal(benchCommand('FOO=bar'), 'FOO=bar');
	equal(
		truncPathTail('/Users/gurio/Source/Projects/brnrd/src/frontend', 20),
		'…/brnrd/src/frontend'
	);
	equal(truncPathTail('src', 20), 'src');
});
