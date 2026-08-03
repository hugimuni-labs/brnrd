import assert from 'node:assert/strict';
import test from 'node:test';

import { RUN_FACE_GLYPHS, runFace, runFacesInWindow } from './runFace.ts';

test('a face is deterministic — same id, same face, forever', () => {
	const a = runFace('run-260802-2028-qzak');
	const b = runFace('run-260802-2028-qzak');
	assert.deepEqual(a, b);
});

test('the mark is a real rune and a real hue', () => {
	const face = runFace('run-260802-1835-pae3');
	assert.ok(RUN_FACE_GLYPHS.includes(face.glyph));
	assert.ok(face.hue >= 0 && face.hue < 360);
	assert.match(face.color, /^hsl\(\d+ 48% 64%\)$/);
});

test('neighbouring ids part ways — the whole point of a face', () => {
	// Two runs from the same evening must not wear the same mark. A hash can
	// collide in principle; these specific ids (the shape every real id has)
	// must not, or the mark stops identifying the very runs shown together.
	const faces = [
		'run-260802-2028-qzak',
		'run-260802-1835-pae3',
		'run-260802-1513-ike7',
		'run-260802-1404-53dh',
		'run-260802-0632-v2ir'
	].map((id) => runFace(id));
	const marks = new Set(faces.map((face) => `${face.glyph}:${face.hue}`));
	assert.equal(marks.size, faces.length);
});

test('an empty id gets the zero face, not a throw', () => {
	const face = runFace('');
	assert.ok(RUN_FACE_GLYPHS.includes(face.glyph));
});

test('the alphabet is the full Elder Futhark, no duplicates', () => {
	assert.equal(RUN_FACE_GLYPHS.length, 24);
	assert.equal(new Set(RUN_FACE_GLYPHS).size, 24);
});

// ── runFacesInWindow — display-time collision re-roll ──────────────────
//
// `run-0`, `run-11` and `run-55` are a real triple-collision found by brute
// force: all three seed to glyph index 15 under `runFace`'s own hash, and
// their hues (159 / 230 / 280) are all distinct — the fixture a hand-picked
// example could not fake, since the whole point is that the probe only
// touches the glyph, never the hue.
const TRIPLE = ['run-0', 'run-11', 'run-55'];

test('a window with no collisions matches plain runFace exactly', () => {
	const ids = ['run-260802-2028-qzak', 'run-260802-1835-pae3', 'run-260802-1513-ike7'];
	const windowed = runFacesInWindow(ids);
	for (const id of ids) {
		assert.deepEqual(windowed.get(id), runFace(id));
	}
});

test('deterministic — same window, same result, every call', () => {
	const a = runFacesInWindow(TRIPLE);
	const b = runFacesInWindow(TRIPLE);
	assert.deepEqual([...a], [...b]);
});

test('a colliding window re-rolls later ids to a free glyph, hue untouched', () => {
	const faces = runFacesInWindow(TRIPLE);
	assert.equal(faces.get('run-0')?.glyph, RUN_FACE_GLYPHS[15]);
	assert.equal(faces.get('run-11')?.glyph, RUN_FACE_GLYPHS[16]);
	assert.equal(faces.get('run-55')?.glyph, RUN_FACE_GLYPHS[17]);
	// The probe never touches hue — every id's hue is exactly what plain
	// `runFace` would give it, collision or not.
	for (const id of TRIPLE) {
		assert.equal(faces.get(id)?.hue, runFace(id).hue);
	}
	// And the three glyphs actually differ — the whole point of the re-roll.
	const glyphs = new Set(TRIPLE.map((id) => faces.get(id)?.glyph));
	assert.equal(glyphs.size, 3);
});

test('order decides who keeps the seed glyph — first occurrence wins', () => {
	const forward = runFacesInWindow(['run-0', 'run-11', 'run-55']);
	const rotated = runFacesInWindow(['run-11', 'run-55', 'run-0']);
	// run-0 leads in `forward` and keeps the seed glyph (15); rotated, run-11
	// leads instead and takes it — the same three ids, a different winner,
	// because the contract is "first in this window's order", not "lowest id".
	assert.equal(forward.get('run-0')?.glyph, RUN_FACE_GLYPHS[15]);
	assert.equal(rotated.get('run-11')?.glyph, RUN_FACE_GLYPHS[15]);
	assert.notEqual(rotated.get('run-0')?.glyph, RUN_FACE_GLYPHS[15]);
});

test('a repeated id in one window keeps one face, not two rolls', () => {
	const faces = runFacesInWindow(['run-0', 'run-11', 'run-0']);
	assert.equal(faces.size, 2);
	assert.deepEqual(faces.get('run-0'), runFace('run-0'));
});

test('empty window is legal and empty', () => {
	assert.equal(runFacesInWindow([]).size, 0);
});

test('overflow past 24 distinct ids collides on purpose, never throws', () => {
	// One id per glyph bucket 0..23 (found by brute force, first hit per
	// bucket), so this list alone already exhausts the alphabet with no
	// collisions — every id gets its own natural seed glyph.
	const fullAlphabet = [
		'run-25',
		'run-31',
		'run-3',
		'run-33',
		'run-1',
		'run-22',
		'run-7',
		'run-8',
		'run-5',
		'run-26',
		'run-34',
		'run-24',
		'run-18',
		'run-2',
		'run-30',
		'run-0',
		'run-32',
		'run-6',
		'run-23',
		'run-4',
		'run-9',
		'run-35',
		'run-27',
		'run-19'
	];
	assert.equal(fullAlphabet.length, 24);
	const withoutOverflow = runFacesInWindow(fullAlphabet);
	assert.equal(new Set([...withoutOverflow.values()].map((f) => f.glyph)).size, 24);

	// The 25th id (`run-55`, seed glyph 15 — the same bucket as `run-0`,
	// already claimed above) has nowhere left to probe to: every glyph in
	// this window is spoken for. It still gets a face — the fallback is its
	// own un-probed seed glyph, which means it now visibly shares a rune
	// with `run-0`, exactly the pigeonhole outcome the window overflowed
	// into, rather than a crash or a silently invented 25th glyph.
	const withOverflow = runFacesInWindow([...fullAlphabet, 'run-55']);
	assert.equal(withOverflow.size, 25);
	assert.equal(withOverflow.get('run-55')?.glyph, RUN_FACE_GLYPHS[15]);
	assert.equal(withOverflow.get('run-55')?.glyph, withOverflow.get('run-0')?.glyph);
});
