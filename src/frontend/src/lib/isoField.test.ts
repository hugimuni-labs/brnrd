// The room's spatial invariants. The diorama's whole claim is that topology
// survives with every label blurred — which makes the *geometry* the
// load-bearing layer: the resident must tower, strands must hold the lane
// in body order, conduits must start and end on the machines they join,
// and the painter must never draw a back machine over a front one.
import { test } from 'node:test';
import { equal, ok } from 'node:assert/strict';

import { buildField } from './residentField.ts';
import {
	boxFaces,
	buildScene,
	floorPath,
	iso,
	paintOrder,
	residentAnatomy,
	sceneBounds,
	strandHeight,
	TILE,
	TRAIL_MAX
} from './isoField.ts';
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

function sceneOf(runs: LiveRun[]) {
	return buildScene(buildField(runs));
}

test('iso projects the 2:1 dimetric camera', () => {
	equal(iso(0, 0).x, 0);
	equal(iso(0, 0).y, 0);
	equal(iso(1, 0).x, TILE);
	equal(iso(1, 0).y, TILE / 2);
	equal(iso(0, 1).x, -TILE);
	equal(iso(0, 1).y, TILE / 2);
	ok(iso(0, 0, 1).y < 0, 'z rises on screen');
});

test('the resident towers over every strand', () => {
	const scene = sceneOf([
		run({ id: 'r1' }),
		run({ id: 's1', is_subspawn: true, parent_run_id: 'r1' }),
		run({
			id: 's2',
			is_subspawn: true,
			parent_run_id: 'r1',
			runner: { class: 'strong' }
		})
	]);
	const resident = scene.machines.find((m) => m.kind === 'resident');
	ok(resident, 'a resident machine exists');
	for (const m of scene.machines) {
		if (m.kind === 'resident') continue;
		ok(resident!.h > m.h * 2, `resident (${resident!.h}) towers over ${m.key} (${m.h})`);
		ok(resident!.w > m.w, 'resident footprint dominates');
	}
});

test('runner class reads at silhouette level', () => {
	ok(
		strandHeight(run({ id: 'a', runner: { class: 'strong' } })) >
			strandHeight(run({ id: 'b', runner: { class: 'balanced' } }))
	);
	ok(
		strandHeight(run({ id: 'b', runner: { class: 'balanced' } })) >
			strandHeight(run({ id: 'c', runner: { class: 'economy' } }))
	);
	// Unknown/absent class lands mid, never zero.
	ok(strandHeight(run({ id: 'd' })) > 0);
});

test('strands hold the lane in body order, and conduits join real ports', () => {
	const scene = sceneOf([
		run({ id: 'r1', started_at: '2026-08-25T20:00:00Z' }),
		run({
			id: 'young',
			is_subspawn: true,
			parent_run_id: 'r1',
			started_at: '2026-08-25T20:20:00Z'
		}),
		run({
			id: 'old',
			is_subspawn: true,
			parent_run_id: 'r1',
			started_at: '2026-08-25T20:05:00Z'
		})
	]);
	const oldM = scene.machines.find((m) => m.key === 'old')!;
	const youngM = scene.machines.find((m) => m.key === 'young')!;
	ok(oldM.x < youngM.x, 'older strand sits nearer the resident');
	equal(oldM.y, youngM.y, 'both hold the lane');

	for (const conduit of scene.conduits) {
		const strand = scene.machines.find((m) => m.key === conduit.key)!;
		const first = conduit.points[0];
		const last = conduit.points[conduit.points.length - 1];
		const resident = scene.machines.find((m) => m.kind === 'resident')!;
		ok(
			first.x >= resident.x && first.x <= resident.x + resident.w,
			'conduit starts on the resident footprint edge'
		);
		equal(first.y, resident.y + resident.d, 'conduit exits the front face');
		ok(last.x >= strand.x && last.x <= strand.x + strand.w, 'conduit ends on its strand');
		equal(last.y, strand.y, 'conduit enters the back face');
	}
	// Parallel conduits comb — no two share a corridor line.
	const corridors = scene.conduits.map((c) => c.points[1].y);
	equal(new Set(corridors).size, corridors.length);
});

test('an orphan root parks by the back wall, connected to nothing', () => {
	const scene = sceneOf([
		run({ id: 'r1' }),
		run({ id: 'ghost', is_subspawn: true, parent_run_id: 'gone' })
	]);
	const ghost = scene.machines.find((m) => m.key === 'ghost')!;
	equal(ghost.kind, 'orphan');
	ok(
		scene.conduits.every((c) => c.key !== 'ghost'),
		'no conduit pretends a parent'
	);
});

test('the platform never clips a machine, and the empty room still stands', () => {
	const wide = sceneOf([
		run({ id: 'r1' }),
		...Array.from({ length: 5 }, (_, i) =>
			run({ id: `s${i}`, is_subspawn: true, parent_run_id: 'r1' })
		)
	]);
	for (const m of wide.machines) {
		ok(m.x + m.w <= wide.cols, `${m.key} fits cols`);
		ok(m.y + m.d <= wide.rows, `${m.key} fits rows`);
	}
	const empty = sceneOf([]);
	equal(empty.machines.length, 0);
	equal(empty.gatePath.length, 0, 'no resident, no gate feed');
	ok(empty.cols > 0 && empty.rows > 0, 'the floor exists between wakes');
	const bounds = sceneBounds(empty);
	ok(bounds.w > 0 && bounds.h > 0);
});

test('painter draws back-to-front and never flips ties across frames', () => {
	const scene = sceneOf([
		run({ id: 'r1' }),
		run({ id: 'a', is_subspawn: true, parent_run_id: 'r1' }),
		run({ id: 'b', is_subspawn: true, parent_run_id: 'r1' })
	]);
	const ordered = paintOrder(scene.machines);
	for (let i = 1; i < ordered.length; i++) {
		const prev = ordered[i - 1];
		const next = ordered[i];
		ok(
			prev.x + prev.w / 2 + prev.y + prev.d / 2 <= next.x + next.w / 2 + next.y + next.d / 2,
			'depth is monotonic'
		);
	}
	// Same input twice → same order (stability under re-poll).
	equal(
		paintOrder(scene.machines)
			.map((m) => m.key)
			.join(','),
		ordered.map((m) => m.key).join(',')
	);
});

test('faces share their silhouette edges', () => {
	const f = boxFaces(2, 3, 1, 1, 0.5);
	// Top's front corner is where left and right faces meet.
	equal(f.top[2].x, f.frontCorner.x);
	equal(f.top[2].y, f.frontCorner.y);
	ok(f.floorFront.y > f.frontCorner.y, 'floor sits below the lid');
	// Every face is a real quad — four distinct corners. The first cut of
	// this module had the right face's floor-back corner duplicated onto
	// the floor-front one, which rendered every machine as a wedge.
	for (const face of [f.top, f.left, f.right]) {
		const seen = new Set(face.map((p) => `${p.x}:${p.y}`));
		equal(seen.size, 4, 'no face degenerates to a triangle');
	}
	const path = floorPath([
		{ x: 0, y: 0 },
		{ x: 1, y: 0 }
	]);
	ok(path.startsWith('M 0 0 L '), path);
});

// ── the resident's anatomy (the entity round) ───────────────────────────────

test('boxFaces lifts a based box whole — a head is the same box, raised', () => {
	const grounded = boxFaces(1, 1, 0.6, 0.6, 0.5);
	const lifted = boxFaces(1, 1, 0.6, 0.6, 0.5, 1.7);
	for (const face of ['top', 'left', 'right'] as const) {
		for (let i = 0; i < grounded[face].length; i++) {
			equal(lifted[face][i].x, grounded[face][i].x, 'lift never shears sideways');
			ok(lifted[face][i].y < grounded[face][i].y, 'every corner rises');
		}
	}
});

function residentMachine() {
	const scene = sceneOf([run({ id: 'r1' })]);
	const m = scene.machines[0];
	equal(m.kind, 'resident');
	return m;
}

test('the automaton: head floats above the torso, bench stands in front', () => {
	const m = residentMachine();
	const anat = residentAnatomy(m, 'automaton');
	ok(anat.head, 'the automaton has a head');
	ok(anat.head!.z0 > anat.torso.h, 'the head hovers — a visible neck gap');
	ok(anat.bench.y >= m.y + m.d, 'the bench is in front of the machine site');
	// No degenerate boxes — the first wedge-shaped tower cost a drive round.
	for (const box of [anat.torso, anat.head!, anat.bench]) {
		ok(box.w > 0 && box.d > 0 && box.h > 0, 'every box has volume');
	}
	// The figure fits its plinth footprint (bench excepted — it stands off it).
	ok(anat.torso.x >= m.x && anat.torso.x + anat.torso.w <= m.x + m.w);
	ok(anat.torso.y >= m.y && anat.torso.y + anat.torso.d <= m.y + m.d);
});

test('the act-trail slits cross the gate-facing face and never degenerate', () => {
	const anat = residentAnatomy(residentMachine(), 'automaton');
	ok(anat.trailSlits.length > 0 && anat.trailSlits.length <= TRAIL_MAX);
	for (const slit of anat.trailSlits) {
		ok(
			Math.abs(slit.a.x - slit.b.x) > 1 || Math.abs(slit.a.y - slit.b.y) > 1,
			'a slit is a segment, not a point'
		);
	}
	// Newest slot sits highest on the face (screen y ascends down).
	for (let i = 1; i < anat.trailSlits.length; i++) {
		ok(anat.trailSlits[i].a.y > anat.trailSlits[i - 1].a.y, 'trail reads downward');
	}
});

test('the core-glyph study: no head — the face floats above the pedestal', () => {
	const m = residentMachine();
	const anat = residentAnatomy(m, 'glyph');
	equal(anat.head, null);
	ok(anat.torso.h < residentAnatomy(m, 'automaton').torso.h, 'the pedestal is shorter');
	const pedestalTop = iso(anat.torso.x, anat.torso.y, anat.torso.h);
	ok(anat.faceAnchor.y < pedestalTop.y, 'the face hangs above the pedestal');
});
