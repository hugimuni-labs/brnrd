import assert from 'node:assert/strict';
import test from 'node:test';

import { RUN_FACE_GLYPHS, runFace } from './runFace.ts';

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
