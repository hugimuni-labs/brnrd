import assert from 'node:assert/strict';
import test from 'node:test';

import {
	LiveRunsAuthError,
	liveRelicChips,
	liveRunDisplayName,
	moodFace,
	requestRunStop,
	wordmarkMood,
	type LiveRun
} from './liveRuns.ts';

test('live run display prefers the resident-authored name', () => {
	assert.equal(
		liveRunDisplayName({ name: 'run naming', label: 'waking message', kind: 'daemon' }),
		'run naming'
	);
});

test('live run display falls back to a deliberate label', () => {
	assert.equal(
		liveRunDisplayName({ name: '', label: 'a handle-shaped label', kind: 'daemon' }),
		'a handle-shaped label'
	);
});

// #585 review fixup: the producer stopped putting a run's task body in
// `label`, so an un-named run's label is now empty. Without `stream` in the
// chain every card on the board would read "daemon" — the leak closed and
// the panel's legibility with it.
test('an un-named run falls back to its conversation key, not its kind', () => {
	assert.equal(
		liveRunDisplayName({
			name: '',
			label: '',
			stream: 'schedule:release-push-dispatch-tick',
			kind: 'daemon'
		}),
		'schedule:release-push-dispatch-tick'
	);
});

test('kind is the last resort, not the second', () => {
	assert.equal(liveRunDisplayName({ name: '', label: '', stream: '', kind: 'daemon' }), 'daemon');
});

// #476: a tap that gets swallowed must never be silent — the caller can only
// keep that promise if this layer throws something it can name.

function stubFetch(status: number, body: unknown = {}): typeof fetch {
	return (async () =>
		({
			ok: status >= 200 && status < 300,
			status,
			json: async () => body
		}) as Response) as unknown as typeof fetch;
}

test('a parked stop comes back as a pending request', async () => {
	const row = await requestRunStop(
		'run-b',
		stubFetch(200, {
			stop_request: {
				request_id: 'stopreq-1',
				run_id: 'run-b',
				requested_at: null,
				status: 'pending'
			}
		})
	);
	assert.equal(row.run_id, 'run-b');
	assert.equal(row.status, 'pending');
});

test('an expired session is typed, so the cell can say "sign in again"', async () => {
	await assert.rejects(() => requestRunStop('run-b', stubFetch(401)), LiveRunsAuthError);
});

test('a run that ended first says so rather than failing anonymously', async () => {
	await assert.rejects(() => requestRunStop('run-b', stubFetch(404)), /no longer live/);
});

test('the run id is encoded, not interpolated raw', async () => {
	let seen = '';
	const spy = (async (url: string) => {
		seen = url;
		return { ok: true, status: 200, json: async () => ({ stop_request: {} }) } as Response;
	}) as unknown as typeof fetch;
	await requestRunStop('run/../evil', spy);
	assert.ok(!seen.includes('run/../evil'), 'a slash in a handle must not reshape the path');
});

// ── relics-so-far chips (#342) ──────────────────────────────────────

test('relic chips order produce first and keep unknown kinds', () => {
	assert.deepEqual(liveRelicChips({ kb: 1, commit: 2, artifact: 3, pr: 1 }), [
		{ kind: 'commit', count: 2 },
		{ kind: 'pr', count: 1 },
		{ kind: 'kb', count: 1 },
		{ kind: 'artifact', count: 3 }
	]);
});

test('branch and summary never chip — they restate other produce', () => {
	assert.deepEqual(liveRelicChips({ branch: 1, summary: 1, commit: 2 }), [
		{ kind: 'commit', count: 2 }
	]);
});

test('zero, empty, and absent counts render no chips at all', () => {
	assert.deepEqual(liveRelicChips(null), []);
	assert.deepEqual(liveRelicChips(undefined), []);
	assert.deepEqual(liveRelicChips({}), []);
	assert.deepEqual(liveRelicChips({ commit: 0 }), []);
});

// ── mood (#566) ─────────────────────────────────────────────────────
//
// The house rule these pin, from `brr.emotes`' own docstring: an unknown or
// absent mood renders as NOTHING or the bare handle name — never a guessed or
// default face. The frontend owns no emote table, so every glyph here is one
// the wire supplied.

function moodRun(over: Partial<LiveRun>): LiveRun {
	return {
		id: 'p1',
		kind: 'daemon',
		stream: 'telegram:1:',
		label: '',
		name: 'a run',
		run_id: 'run-1',
		repo_label: 'org/repo',
		started_at: '2026-07-23T22:00:00Z',
		last_seen: '2026-07-23T22:00:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: {},
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		...over
	};
}

test('an unknown mood handle degrades to the bare name, never a face', () => {
	// The daemon could not resolve the handle against the emote library, so it
	// published the name with no glyph. That is the whole contract: the chip
	// says the word and shows nothing.
	assert.deepEqual(moodFace('sideways', null, null), {
		name: 'sideways',
		glyph: null,
		sequences: null,
		rest: null,
		pitch: null
	});
});

test('a resolved mood carries its whole cycle, not one frame', () => {
	// The starvation this slice fixed: `mood_glyph` is the animation's *base*
	// and is shared across a whole face family, so a surface with only that
	// renders most of the palette identically. `sequences` is what moves and
	// `rest` is what identifies while still — both come off the wire already
	// resolved, because this frontend owns no emote table.
	const face = moodFace(
		'fo.cus',
		'b·_·d',
		0.45,
		[
			['b·_·d', 'b-_-d', 'b·_·d'],
			['b·_·d', 'bx_xd', 'b·_·d']
		],
		'b·_·d'
	);
	assert.equal(face?.sequences?.length, 2);
	assert.deepEqual(face?.sequences?.[1], ['b·_·d', 'bx_xd', 'b·_·d']);
	assert.equal(face?.rest, 'b·_·d');
});

test('meaningless frame payloads are dropped, not passed on as empty cycles', () => {
	// A caller checking `sequences?.length` should never have to also know
	// that `[[]]` means nothing. Bounds at the wire stop a hostile payload;
	// this stops a merely useless one.
	assert.equal(moodFace('id_l', '(-_-)', 0.2, [])?.sequences, null);
	assert.equal(moodFace('id_l', '(-_-)', 0.2, [[]])?.sequences, null);
	assert.equal(moodFace('id_l', '(-_-)', 0.2, [['  ', '']])?.sequences, null);
	assert.deepEqual(moodFace('id_l', '(-_-)', 0.2, [['a'], []])?.sequences, [['a']]);
	// A whitespace-only rest is absent, same rule as the glyph.
	assert.equal(moodFace('id_l', '(-_-)', 0.2, null, '   ')?.rest, null);
});

test('an absent mood is not a mood — the surfaces render nothing', () => {
	assert.equal(moodFace(null), null);
	assert.equal(moodFace(undefined, '(・_・)', 0.5), null);
	assert.equal(moodFace('   ', '(・_・)'), null);
});

test('a glyph is worn only when the wire carried one', () => {
	assert.deepEqual(moodFace('id_l', '(-_-)', 0.25), {
		name: 'id_l',
		glyph: '(-_-)',
		sequences: null,
		rest: null,
		pitch: 0.25
	});
	// Whitespace-only is the same as absent; a pitch that isn't a real number
	// is dropped rather than tinting off a NaN.
	assert.equal(moodFace('id_l', '  ')?.glyph, null);
	assert.equal(moodFace('id_l', '(-_-)', Number.NaN)?.pitch, null);
});

test('the wordmark wears the newest live mood, not the first one it finds', () => {
	const runs = [
		moodRun({ id: 'old', started_at: '2026-07-23T21:00:00Z', mood: 'gnaw', mood_glyph: '>_<' }),
		moodRun({ id: 'new', started_at: '2026-07-23T22:30:00Z', mood: 'id_l', mood_glyph: '(-_-)' })
	];
	assert.deepEqual(wordmarkMood(runs, null), { frames: ['(-_-)'], pitch: null });
});

test('a live run with real frames drives the wordmark with them, not its glyph', () => {
	// Before `mood_frames` the live branch could only hand over `[glyph]` —
	// a one-frame "cycle", i.e. a still image where the daemon's own face
	// animated. The single-glyph path below is now only the pre-upgrade
	// fallback.
	const runs = [
		moodRun({
			id: 'live',
			started_at: '2026-07-23T22:30:00Z',
			mood: 'fo.cus',
			mood_glyph: 'b·_·d',
			mood_frames: [
				['b·_·d', 'b-_-d', 'b·_·d'],
				['b·_·d', 'bx_xd', 'b·_·d']
			],
			mood_pitch: 0.45
		})
	];
	// The mark wears one face at a time: the primary cycle, alternates are
	// the chip's business.
	assert.deepEqual(wordmarkMood(runs, null), {
		frames: ['b·_·d', 'b-_-d', 'b·_·d'],
		pitch: 0.45
	});
});

test('runs without a mood are skipped, and a moodless board falls to the daemon', () => {
	const daemon = {
		state: 'idle',
		name: 'brnrd breathing',
		glyph: '(-_-)',
		frames: ['(-_-)', '(-.-)'],
		pitch: 0.4
	};
	assert.deepEqual(wordmarkMood([moodRun({})], daemon), {
		frames: ['(-_-)', '(-.-)'],
		pitch: 0.4
	});
	assert.deepEqual(wordmarkMood(null, daemon), { frames: ['(-_-)', '(-.-)'], pitch: 0.4 });
});

test('an unknown live mood still tints, because the pitch is not a guess', () => {
	// Name-only on the wire: no face to show, but the body axis the daemon
	// reported is still honest telemetry.
	const runs = [moodRun({ mood: 'sideways', mood_glyph: null, mood_pitch: 0.9 })];
	assert.deepEqual(wordmarkMood(runs, null), { frames: null, pitch: 0.9 });
});

test('no mood anywhere leaves the wordmark alone — no frames, no tint', () => {
	assert.deepEqual(wordmarkMood(null, null), { frames: null, pitch: null });
	assert.deepEqual(wordmarkMood([moodRun({})], null), { frames: null, pitch: null });
	// A daemon that published an empty frame list is the same as one that
	// published none: the wordmark keeps its own wink.
	assert.deepEqual(
		wordmarkMood(null, { state: 'idle', name: 'brnrd', glyph: '', frames: [], pitch: 0 }),
		{ frames: null, pitch: 0 }
	);
});

test('a null daemon mood leaves the loom idle seam exactly as it was', () => {
	// The seam swaps its hollow dot for the resting face only when this is
	// non-null; a pre-upgrade daemon publishes nothing and gets today's render.
	const restingFace = (mood: { name: string; glyph: string } | null) =>
		mood ? moodFace(mood.name, mood.glyph) : null;
	assert.equal(restingFace(null), null);
	assert.deepEqual(restingFace({ name: 'brnrd breathing', glyph: '(-_-)' }), {
		name: 'brnrd breathing',
		glyph: '(-_-)',
		sequences: null,
		rest: null,
		pitch: null
	});
});
