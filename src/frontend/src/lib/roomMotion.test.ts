import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, fileFromDetail, type TrailStep } from './roomGraph.ts';
import { compileTopology, dirId } from './roomTopology.ts';
import { emptyAtlas, layoutRoom } from './roomLayout.ts';
import { advanceWalks, diffTransitions, easeCamera, walkFor, walkPositions } from './roomMotion.ts';
import { referenceFrames } from './referenceTrace.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';

const REPO = 'hugimuni-labs/brnrd';

function liveWire(runs: LiveRun[]): LiveRunsResponse {
	return {
		generated_at: '2026-08-27T10:20:00Z',
		runs,
		stale: false,
		reported_at: '2026-08-27T10:20:00Z',
		spawn_max_concurrent: 3
	};
}

function recordTrails(trails: Record<string, TrailStep[]>, runs: LiveRun[]) {
	for (const run of runs) {
		const dir = run.edge?.dir && run.edge.dir !== '.' ? run.edge.dir : null;
		const at = run.edge?.at ?? null;
		if (!dir || !at) continue;
		const trail = (trails[run.run_id] ??= []);
		if (trail.some((s) => s.at === at)) continue;
		trail.push({ dir, act: run.edge?.act ?? null, at, file: fileFromDetail(run.edge?.detail) });
	}
}

function sceneAt(frameIdx: number, trails: Record<string, TrailStep[]>) {
	const frames = referenceFrames();
	for (const f of frames.slice(0, frameIdx + 1)) recordTrails(trails, f);
	const graph = compileRoomGraph(liveWire(frames[frameIdx]), null, trails);
	const topo = compileTopology(graph);
	const { layout } = layoutRoom(topo, emptyAtlas());
	return { graph, topo, layout };
}

const RESIDENT = 'run-260827-1000-ref1';

test('a place change mints exactly one transition receipt with the trie route', () => {
	const trails: Record<string, TrailStep[]> = {};
	const a = sceneAt(0, trails); // #81 at src/frontend
	const b = sceneAt(1, trails); // #82 at src/frontend/src/lib
	const transitions = diffTransitions(b.topo.actorPlaces, b.topo.actorPlaces, b.topo);
	assert.deepEqual(transitions, [], 'same places ⇒ no receipts');
	const moved = diffTransitions(a.topo.actorPlaces, b.topo.actorPlaces, b.topo);
	assert.equal(moved.length, 1);
	assert.equal(moved[0].actorRunId, RESIDENT);
	// The route still walks the trie through the lowest common ancestor —
	// that is the property. It is one hop shorter since the trie fold
	// (2026-08-29): `src/frontend/src` had a single child and folded into it,
	// so there is no longer a scaffolding node to step through. The endpoints
	// keep their ids, because the fold always keeps the *deep* one.
	assert.deepEqual(moved[0].route, [
		dirId(REPO, ['src', 'frontend']),
		dirId(REPO, ['src', 'frontend', 'src', 'lib'])
	]);
});

test('a new actor appears rather than walking a fabricated route', () => {
	const trails: Record<string, TrailStep[]> = {};
	const before = sceneAt(2, trails); // resident alone
	const after = sceneAt(3, trails); // + the strand on the knowledge island
	const transitions = diffTransitions(before.topo.actorPlaces, after.topo.actorPlaces, after.topo);
	const strandT = transitions.find((t) => t.actorRunId === 'run-260827-1012-des1');
	assert.ok(strandT);
	assert.equal(strandT.fromPlaceId, null);
	assert.deepEqual(strandT.route, [strandT.toPlaceId]);
});

test('a walk follows the layout edge polylines and ends at the destination', () => {
	const trails: Record<string, TrailStep[]> = {};
	const a = sceneAt(0, trails);
	const b = sceneAt(1, trails);
	const [t] = diffTransitions(a.topo.actorPlaces, b.topo.actorPlaces, b.topo);
	const walk = walkFor(t, b.layout);
	assert.ok(walk && walk.points.length >= 2);
	const dest = b.layout.nodes[t.toPlaceId];
	assert.deepEqual(walk.points[walk.points.length - 1], dest);
	// every hop is short — reads as travel, not teleport
	for (let i = 1; i < walk.points.length; i++) {
		const dx = Math.abs(walk.points[i].x - walk.points[i - 1].x);
		const dy = Math.abs(walk.points[i].y - walk.points[i - 1].y);
		assert.ok(Math.max(dx, dy) <= 3, `hop ${i} too long`);
	}
});

test('advanceWalks moves each walk one step and drops it on arrival', () => {
	const trails: Record<string, TrailStep[]> = {};
	const a = sceneAt(0, trails);
	const b = sceneAt(1, trails);
	const [t] = diffTransitions(a.topo.actorPlaces, b.topo.actorPlaces, b.topo);
	let walks = [walkFor(t, b.layout)!];
	const first = walkPositions(walks)[RESIDENT];
	assert.deepEqual(first, walks[0].points[0]);
	let guard = 0;
	while (walks.length > 0 && guard < 100) {
		const adv = advanceWalks(walks);
		walks = adv.walks;
		guard++;
	}
	assert.ok(guard > 1 && guard < 100, 'walk completes in a bounded number of ticks');
});

test('easeCamera converges and snaps byte-stable at the target', () => {
	let cam = { x: 0, y: 0 };
	const target = { x: 40, y: -12 };
	let guard = 0;
	while ((cam.x !== target.x || cam.y !== target.y) && guard < 60) {
		cam = easeCamera(cam, target);
		guard++;
	}
	assert.deepEqual(cam, target);
	assert.ok(guard > 1 && guard < 60);
	// at the target it stays exactly there — no oscillation
	assert.deepEqual(easeCamera(cam, target), target);
});
