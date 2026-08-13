import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';

import {
	blockedItems,
	blockers,
	blockersOnYou,
	buildWarpGraph,
	completedItems,
	contributingCone,
	findGoalReadingsFile,
	formatReadingValue,
	goalItems,
	goalReadingsPath,
	isBlocked,
	isGoalReadingsFile,
	isRunTopicsFile,
	isTopicFile,
	isWarpItemFile,
	itemInTopics,
	liveTakenRuns,
	parseGoalReadings,
	parseRunTopics,
	parseWarpItem,
	parseWarpTopic,
	readingsNewestFirst,
	readyItems,
	resolveTopics,
	runIdForTopicsPath,
	runTopicIndex,
	summarizeGoalReadings,
	topicCounts,
	topicFace,
	topicFaces,
	topicThreads,
	dependents,
	RUNE_SPACE,
	MIN_HUE_GAP
} from './warpGraph.ts';
import type { SurfaceFile } from './surface.ts';

function file(path: string, markdown: string): SurfaceFile {
	return { path, markdown, layer: 'authored', truncated: false };
}

const TOPIC_LOOM = file(
	'surface/topics/loom.md',
	'# The loom\n\nThe dashboard becomes the machine it renders.'
);
const TOPIC_POST = file(
	'surface/topics/post.md',
	'# The post\n\nids: mail\n\nChannels and delivery.'
);

function graphOf(...files: SurfaceFile[]) {
	return buildWarpGraph(files);
}

describe('file discovery', () => {
	it('recognizes item and topic files, skipping index and nested paths', () => {
		assert.equal(isWarpItemFile('surface/warp/w-1.md'), true);
		assert.equal(isWarpItemFile('surface/warp/index.md'), false);
		assert.equal(isWarpItemFile('surface/warp/sub/w-1.md'), false);
		assert.equal(isWarpItemFile('surface/layers/w-1.md'), false);
		assert.equal(isTopicFile('surface/topics/loom.md'), true);
		assert.equal(isTopicFile('surface/topics/index.md'), false);
	});

	it('recognizes a run topics file by its exact four-segment shape', () => {
		assert.equal(isRunTopicsFile('runs/hugimuni-labs__brnrd/run-a/topics.md'), true);
		assert.equal(isRunTopicsFile('runs/hugimuni-labs__brnrd/run-a/state.md'), false);
		assert.equal(isRunTopicsFile('runs/hugimuni-labs__brnrd/run-a/messages/topics.md'), false);
		assert.equal(isRunTopicsFile('runs/only-one-slug/topics.md'), false);
		assert.equal(isRunTopicsFile('surface/topics/topics.md'), false);
	});
});

describe('run topics.md', () => {
	it('parses the topics: row, splitIds grammar', () => {
		assert.deepEqual(parseRunTopics('# Topics\n\ntopics: loom post\n'), ['loom', 'post']);
		assert.deepEqual(parseRunTopics(''), []);
	});

	it('runIdForTopicsPath names the run, null for any other path', () => {
		assert.equal(runIdForTopicsPath('runs/hugimuni-labs__brnrd/run-a/topics.md'), 'run-a');
		assert.equal(runIdForTopicsPath('runs/hugimuni-labs__brnrd/run-a/state.md'), null);
	});
});

describe('parseWarpItem', () => {
	it('parses the full row block and body', () => {
		const item = parseWarpItem(
			'surface/warp/w-7.md',
			[
				'# Ship the digest',
				'',
				'type: action',
				'topics: loom post',
				'needs: w-3 w-4',
				'refs: hugimuni-labs/brnrd#1256',
				'prompt: Build the digest per the signed design.',
				'taken: run-260810-0001-aaaa',
				'',
				'Body prose here.'
			].join('\n')
		);
		assert.equal(item.id, 'w-7');
		assert.equal(item.headline, 'Ship the digest');
		assert.equal(item.type, 'action');
		assert.deepEqual(item.topics, ['loom', 'post']);
		assert.deepEqual(item.needs, ['w-3', 'w-4']);
		assert.equal(item.state, 'open');
		assert.deepEqual(item.taken, ['run-260810-0001-aaaa']);
		assert.equal(item.refs[0].href, 'https://github.com/hugimuni-labs/brnrd/issues/1256');
		assert.equal(item.prompt, 'Build the digest per the signed design.');
		assert.equal(item.bodyMarkdown, 'Body prose here.');
	});

	it('derives state from the receipt rows — done wins, retired second, no state row exists', () => {
		const done = parseWarpItem(
			'surface/warp/w-2.md',
			'# X\n\ntype: decision\ndone: 2026-08-11 run-260811-1114-z0xq\n'
		);
		assert.equal(done.state, 'done');
		assert.equal(done.doneDate, '2026-08-11');
		assert.equal(done.doneRun, 'run-260811-1114-z0xq');

		const retired = parseWarpItem('surface/warp/w-3.md', '# X\n\nretired: 2026-08-10 superseded\n');
		assert.equal(retired.state, 'retired');
		assert.equal(retired.retiredNote, '2026-08-10 superseded');

		const open = parseWarpItem('surface/warp/w-4.md', '# X\n');
		assert.equal(open.state, 'open');
	});

	it('treats an unknown type as untyped, never coerced', () => {
		const item = parseWarpItem('surface/warp/w-5.md', '# X\n\ntype: chore\n');
		assert.equal(item.type, null);
	});

	it('ends the row block at the first unrecognized line', () => {
		const item = parseWarpItem(
			'surface/warp/w-6.md',
			'# X\n\ntype: action\nnot a row\nneeds: w-1\n'
		);
		assert.equal(item.type, 'action');
		assert.deepEqual(item.needs, []);
		assert.match(item.bodyMarkdown, /not a row/);
	});

	it('falls back to the id as headline', () => {
		const item = parseWarpItem('surface/warp/w-9.md', 'type: action\n');
		assert.equal(item.headline, 'w-9');
	});
});

describe('parseWarpTopic', () => {
	it('carries the alias set, canonical first, never duplicated', () => {
		const topic = parseWarpTopic('surface/topics/post.md', '# The post\n\nids: mail post\n\nBody.');
		assert.equal(topic.canonicalId, 'post');
		assert.deepEqual(topic.ids, ['post', 'mail']);
		assert.equal(topic.definitionMarkdown, 'Body.');
	});

	it('reads split-into as the retirement breadcrumb', () => {
		const topic = parseWarpTopic('surface/topics/old.md', '# Old\n\nsplit-into: a b\n');
		assert.deepEqual(topic.splitInto, ['a', 'b']);
	});
});

describe('the graph', () => {
	const files = [
		TOPIC_LOOM,
		TOPIC_POST,
		file('surface/warp/w-1.md', '# Decide the shape\n\ntype: decision\ntopics: loom\n'),
		file(
			'surface/warp/w-2.md',
			'# Build it\n\ntype: action\ntopics: loom\nneeds: w-1\nprompt: build\n'
		),
		file('surface/warp/w-3.md', '# Mail thing\n\ntype: action\ntopics: mail\n'),
		file(
			'surface/warp/w-4.md',
			'# Done thing\n\ntype: action\ntopics: post\ndone: 2026-08-10 run-260810-0001-aaaa\n'
		),
		file('surface/warp/w-5.md', '# Dangling\n\ntype: action\nneeds: w-99\n')
	];
	const graph = graphOf(...files);

	it('resolves topic aliases to canonical topics', () => {
		const mailItem = graph.itemById.get('w-3')!;
		const topics = resolveTopics(mailItem, graph);
		assert.equal(topics.length, 1);
		assert.equal(topics[0].canonicalId, 'post');
	});

	it('derives blocked from open edges only — a done blocker frees', () => {
		assert.equal(isBlocked(graph.itemById.get('w-2')!, graph), true);
		const doneBlocking = graphOf(
			file('surface/warp/w-1.md', '# A\n\ntype: decision\ndone: 2026-08-01\n'),
			file('surface/warp/w-2.md', '# B\n\ntype: action\nneeds: w-1\n')
		);
		assert.equal(isBlocked(doneBlocking.itemById.get('w-2')!, doneBlocking), false);
	});

	it('a dangling edge warns, never blocks', () => {
		const item = graph.itemById.get('w-5')!;
		assert.equal(isBlocked(item, graph), false);
		assert.deepEqual(blockers(item, graph).dangling, ['w-99']);
	});

	it('bands: ready decisions first, blocked below, completed apart', () => {
		const ready = readyItems(graph).map((item) => item.id);
		assert.deepEqual(ready, ['w-1', 'w-3', 'w-5']);
		assert.deepEqual(
			blockedItems(graph).map((item) => item.id),
			['w-2']
		);
		assert.deepEqual(
			completedItems(graph).map((item) => item.id),
			['w-4']
		);
	});

	it('dependents answers the unblocks direction', () => {
		assert.deepEqual(
			dependents(graph.itemById.get('w-1')!, graph).map((item) => item.id),
			['w-2']
		);
	});

	it('numeric-aware ordering keeps w-2 before w-10', () => {
		const wide = graphOf(
			file('surface/warp/w-10.md', '# Ten\n\ntype: action\n'),
			file('surface/warp/w-2.md', '# Two\n\ntype: action\n')
		);
		assert.deepEqual(
			readyItems(wide).map((item) => item.id),
			['w-2', 'w-10']
		);
	});

	it('topic filter: untagged passes only the all-lit filter', () => {
		const untagged = graph.itemById.get('w-5')!;
		assert.equal(itemInTopics(untagged, graph, null), true);
		assert.equal(itemInTopics(untagged, graph, new Set(['loom'])), false);
		const loomItem = graph.itemById.get('w-1')!;
		assert.equal(itemInTopics(loomItem, graph, new Set(['loom'])), true);
		assert.equal(itemInTopics(loomItem, graph, new Set(['post'])), false);
	});

	it('runTopicIndex joins taken and done runs to canonical topic ids', () => {
		const index = runTopicIndex(graph);
		assert.deepEqual(index.get('run-260810-0001-aaaa'), ['post']);
	});

	it("runTopicIndex unions a run's own topics.md claim for a run with no item edge", () => {
		const withClaim = [
			...files,
			file('runs/hugimuni-labs__brnrd/run-chat/topics.md', '# Topics\n\ntopics: loom\n')
		];
		const index = runTopicIndex(graphOf(...files), withClaim);
		assert.deepEqual(index.get('run-chat'), ['loom']);
	});

	it('runTopicIndex dedups a run both taken-by-item and claimed by its own topics.md', () => {
		const claimFiles = [
			...files,
			file('runs/hugimuni-labs__brnrd/run-260810-0001-aaaa/topics.md', '# Topics\n\ntopics: post\n')
		];
		const index = runTopicIndex(graph, claimFiles);
		assert.deepEqual(index.get('run-260810-0001-aaaa'), ['post']);
	});

	it('runTopicIndex: an unknown topic id in a topics.md is dropped silently', () => {
		const claimFiles = [
			...files,
			file(
				'runs/hugimuni-labs__brnrd/run-chat/topics.md',
				'# Topics\n\ntopics: loom no-such-topic\n'
			)
		];
		const index = runTopicIndex(graph, claimFiles);
		assert.deepEqual(index.get('run-chat'), ['loom']);
	});

	it('runTopicIndex ignores paths that are not a run topics.md', () => {
		const notATopicsFile = [
			...files,
			file('runs/hugimuni-labs__brnrd/run-chat/state.md', 'topics: loom\n')
		];
		const index = runTopicIndex(graph, notATopicsFile);
		assert.equal(index.has('run-chat'), false);
	});

	it('topicCounts splits ready/blocked per canonical id, untagged under empty key', () => {
		const counts = topicCounts(graph);
		assert.deepEqual(counts.get('loom'), { ready: 1, blocked: 1 });
		assert.deepEqual(counts.get('post'), { ready: 1, blocked: 0 });
		assert.deepEqual(counts.get(''), { ready: 1, blocked: 0 });
	});

	it('topicThreads drops split breadcrumbs and carries stable faces', () => {
		const withSplit = graphOf(
			TOPIC_LOOM,
			file('surface/topics/old.md', '# Old\n\nsplit-into: loom\n')
		);
		const threads = topicThreads(withSplit);
		assert.deepEqual(
			threads.map((thread) => thread.canonicalId),
			['loom']
		);
		// Face is a pure function of the canonical id — stable across set
		// changes, unlike the index-based hue this replaces.
		assert.deepEqual(
			threads[0].face,
			topicFace(withSplit.topics.find((t) => t.canonicalId === 'loom')!)
		);
	});

	it('topicFaces are unique within the rune space — the set-probed cap', () => {
		// 20 topics (< RUNE_SPACE): the probe must hand every topic its own
		// stave, whatever the hashes collide on.
		const files = Array.from({ length: 20 }, (_, i) =>
			file(`surface/topics/topic-${i}.md`, `# Topic ${i}\n`)
		);
		const g = graphOf(...files);
		const faces = topicFaces(g);
		const glyphs = [...faces.values()].map((face) => face.glyph);
		assert.equal(new Set(glyphs).size, glyphs.length);
		assert.ok(glyphs.length <= RUNE_SPACE);
	});

	function circularMinGap(hues: number[]): number {
		const sorted = [...hues].sort((a, b) => a - b);
		return Math.min(
			...sorted.map((h, i) => (i === 0 ? h + 360 - sorted[sorted.length - 1] : h - sorted[i - 1]))
		);
	}

	it('topicFaces spreads hues to a minimum angular distance — his "6 topics land neighbors" case', () => {
		const files = Array.from({ length: 6 }, (_, i) =>
			file(`surface/topics/topic-${i}.md`, `# Topic ${i}\n`)
		);
		const g = graphOf(...files);
		const faces = topicFaces(g);
		const hues = [...faces.values()].map((face) => face.hue);
		assert.equal(new Set(hues).size, hues.length, 'no two topics share a hue');
		// 6 <= 12, so the full-circle target (360/6 = 60°) clears MIN_HUE_GAP
		// with room to spare — assert the documented floor, not the exact
		// target (the wrap-seam correction can shave a couple of degrees off
		// individual gaps; see the doc comment on `separateHues`).
		assert.ok(
			circularMinGap(hues) >= MIN_HUE_GAP,
			`min gap ${circularMinGap(hues)} below the documented floor ${MIN_HUE_GAP}`
		);
	});

	it('topicFaces gracefully falls back to even spacing past the separable count', () => {
		// More topics than 360 / MIN_HUE_GAP can hold apart at the full gap —
		// pigeonhole, same shape as the glyph probe's alphabet-exhausted case.
		// The pass still keeps every hue distinct and roughly evenly spaced
		// rather than leaving any pair bunched.
		const files = Array.from({ length: 24 }, (_, i) =>
			file(`surface/topics/topic-${i}.md`, `# Topic ${i}\n`)
		);
		const g = graphOf(...files);
		const hues = [...topicFaces(g).values()].map((face) => face.hue);
		assert.equal(new Set(hues).size, hues.length);
		assert.ok(circularMinGap(hues) >= 10, `min gap collapsed: ${circularMinGap(hues)}`);
	});

	it('a single topic is untouched by the hue pass — no neighbour to separate from', () => {
		const g = graphOf(TOPIC_LOOM);
		const faces = topicFaces(g);
		assert.deepEqual(faces.get('loom'), topicFace(g.topics.find((t) => t.canonicalId === 'loom')!));
	});

	it('liveTakenRuns frames only currently-live holders', () => {
		const item = parseWarpItem('surface/warp/w-8.md', '# X\n\ntaken: run-a run-b\n');
		assert.deepEqual(liveTakenRuns(item, new Set(['run-b'])), ['run-b']);
	});

	it('alias collisions resolve to the first topic, deterministically', () => {
		const collided = graphOf(
			file('surface/topics/a.md', '# A\n\nids: shared\n'),
			file('surface/topics/b.md', '# B\n\nids: shared\n')
		);
		assert.equal(collided.topicByAlias.get('shared')!.canonicalId, 'a');
	});
});

describe('goal node (design-goal-oriented-engineering.md)', () => {
	it('parses the three goal-only free-text rows', () => {
		const item = parseWarpItem(
			'surface/warp/g-1.md',
			'# Grow attention\n\ntype: goal\nmetric: tickets bought\ntarget: 1000/mo\nhorizon: Q4 2026\n'
		);
		assert.equal(item.type, 'goal');
		assert.equal(item.metric, 'tickets bought');
		assert.equal(item.target, '1000/mo');
		assert.equal(item.horizon, 'Q4 2026');
		assert.equal(item.state, 'open');
	});

	it('advances: is the same list grammar as needs:, legal on any item', () => {
		const item = parseWarpItem(
			'surface/warp/w-1.md',
			'# Ship it\n\ntype: action\nadvances: g-1 g-2\n'
		);
		assert.deepEqual(item.advances, ['g-1', 'g-2']);
	});

	it('advances: is legal on a goal itself (a sub-goal edge)', () => {
		const item = parseWarpItem('surface/warp/g-2.md', '# Sub-goal\n\ntype: goal\nadvances: g-1\n');
		assert.deepEqual(item.advances, ['g-1']);
	});

	it('goalItems bands goals apart from readyItems/blockedItems', () => {
		const g = graphOf(
			file('surface/warp/g-1.md', '# Grow attention\n\ntype: goal\n'),
			file('surface/warp/w-1.md', '# Decide\n\ntype: decision\n')
		);
		assert.deepEqual(
			goalItems(g).map((item) => item.id),
			['g-1']
		);
		assert.deepEqual(
			readyItems(g).map((item) => item.id),
			['w-1']
		);
		assert.deepEqual(blockedItems(g), []);
	});

	it('contributingCone: direct advancers plus their transitive needs closure', () => {
		const g = graphOf(
			file('surface/warp/g-1.md', '# Grow attention\n\ntype: goal\n'),
			file('surface/warp/w-1.md', '# Ship the digest\n\ntype: action\nadvances: g-1\nneeds: w-2\n'),
			file('surface/warp/w-2.md', '# Instrument analytics\n\ntype: preparation\n'),
			file('surface/warp/w-3.md', '# Unrelated\n\ntype: action\n')
		);
		const cone = new Set(contributingCone('g-1', g).map((item) => item.id));
		assert.deepEqual(cone, new Set(['w-1', 'w-2']));
	});

	it("contributingCone does not recurse through a sub-goal's own advancers", () => {
		const g = graphOf(
			file('surface/warp/g-1.md', '# Parent goal\n\ntype: goal\n'),
			file('surface/warp/g-2.md', '# Sub-goal\n\ntype: goal\nadvances: g-1\n'),
			file('surface/warp/w-1.md', '# Work on the sub-goal\n\ntype: action\nadvances: g-2\n')
		);
		const cone = contributingCone('g-1', g).map((item) => item.id);
		assert.deepEqual(cone, ['g-2']);
	});

	it('blockersOnYou: open decisions/preparations inside the cone, done ones excluded', () => {
		const g = graphOf(
			file('surface/warp/g-1.md', '# Grow attention\n\ntype: goal\n'),
			file('surface/warp/w-1.md', '# Ship\n\ntype: action\nadvances: g-1\nneeds: w-2 w-3\n'),
			file('surface/warp/w-2.md', '# Pick the metric\n\ntype: decision\n'),
			file('surface/warp/w-3.md', '# Already decided\n\ntype: decision\ndone: 2026-08-11\n')
		);
		assert.deepEqual(
			blockersOnYou('g-1', g).map((item) => item.id),
			['w-2']
		);
	});

	it('topicCounts excludes goals — they are not item-lane rows', () => {
		const g = graphOf(
			TOPIC_LOOM,
			file('surface/warp/g-1.md', '# Grow attention\n\ntype: goal\ntopics: loom\n')
		);
		assert.equal(topicCounts(g).get('loom'), undefined);
	});
});

describe('goal readings (design-goal-oriented-engineering.md §"a metrics block in the wake")', () => {
	it('isGoalReadingsFile matches only a g-<N> readings file directly under warp/', () => {
		assert.equal(isGoalReadingsFile('surface/warp/g-1.readings.jsonl'), true);
		assert.equal(isGoalReadingsFile('surface/warp/g-12.readings.jsonl'), true);
		assert.equal(isGoalReadingsFile('surface/warp/w-1.readings.jsonl'), false);
		assert.equal(isGoalReadingsFile('surface/warp/g-1.md'), false);
		assert.equal(isGoalReadingsFile('surface/warp/nested/g-1.readings.jsonl'), false);
		assert.equal(isGoalReadingsFile('surface/topics/g-1.readings.jsonl'), false);
	});

	it('goalReadingsPath/findGoalReadingsFile locate the sibling file by path', () => {
		const readingsFile = file(
			'surface/warp/g-1.readings.jsonl',
			'{"ts": "2026-08-01T00:00:00Z", "key": "tickets", "value": 10, "source": "m"}'
		);
		assert.equal(goalReadingsPath('g-1'), 'surface/warp/g-1.readings.jsonl');
		assert.equal(findGoalReadingsFile('g-1', [readingsFile]), readingsFile);
		assert.equal(findGoalReadingsFile('g-2', [readingsFile]), null);
	});

	it('parseGoalReadings skips malformed lines rather than throwing', () => {
		const markdown = [
			'{"ts": "2026-08-01T00:00:00Z", "key": "tickets", "value": 10, "source": "m"}',
			'not json at all',
			'{"ts": "2026-08-02T00:00:00Z", "key": "tickets"}', // missing value
			'{"ts": "2026-08-03T00:00:00Z", "key": "tickets", "value": 15, "source": "m", "note": "spike"}',
			''
		].join('\n');
		const readings = parseGoalReadings(markdown);
		assert.deepEqual(
			readings.map((r) => r.value),
			[10, 15]
		);
		assert.equal(readings[1].note, 'spike');
		assert.equal(readings[0].note, null);
	});

	it('summarizeGoalReadings: latest/previous/delta/count/min/max, sorted by ts not append order', () => {
		const readings = parseGoalReadings(
			[
				'{"ts": "2026-08-03T00:00:00Z", "key": "tickets", "value": 15, "source": "m"}',
				'{"ts": "2026-08-01T00:00:00Z", "key": "tickets", "value": 10, "source": "m"}',
				'{"ts": "2026-08-02T00:00:00Z", "key": "conversion", "value": 0.5, "source": "m"}'
			].join('\n')
		);
		const summary = summarizeGoalReadings(readings);
		const tickets = summary.get('tickets')!;
		assert.equal(tickets.latest.value, 15);
		assert.equal(tickets.previous?.value, 10);
		assert.equal(tickets.delta, 5);
		assert.equal(tickets.count, 2);
		assert.equal(tickets.min, 10);
		assert.equal(tickets.max, 15);
		const conversion = summary.get('conversion')!;
		assert.equal(conversion.previous, null);
		assert.equal(conversion.delta, null);
		assert.equal(conversion.count, 1);
	});

	it('readingsNewestFirst orders by ts descending without mutating the input', () => {
		const readings = parseGoalReadings(
			[
				'{"ts": "2026-08-01T00:00:00Z", "key": "tickets", "value": 10, "source": "m"}',
				'{"ts": "2026-08-03T00:00:00Z", "key": "tickets", "value": 15, "source": "m"}'
			].join('\n')
		);
		const ordered = readingsNewestFirst(readings);
		assert.deepEqual(
			ordered.map((r) => r.ts),
			['2026-08-03T00:00:00Z', '2026-08-01T00:00:00Z']
		);
		// input order untouched
		assert.equal(readings[0].ts, '2026-08-01T00:00:00Z');
	});

	it('formatReadingValue trims integers and trailing zeros', () => {
		assert.equal(formatReadingValue(10), '10');
		assert.equal(formatReadingValue(12.5), '12.5');
		assert.equal(formatReadingValue(0.1), '0.1');
	});

	// `tests/fixtures/goal_readings_sample.jsonl` is real `brnrd goal record`
	// output (captured from a scratch account, three calls: two `tickets`
	// samples, one `conversion` sample) — not hand-written here. The same
	// file also round-trips through `items.load_readings` in
	// `tests/test_items.py`, so the Python writer and this reader are
	// checked against one grammar instead of two independently plausible
	// ones (same cross-language-fixture shape as `card_now_projection.json`
	// / `runNode.test.ts` above).
	it('parses real brnrd goal record output byte-for-byte', () => {
		const raw = readFileSync(
			new URL('../../../../tests/fixtures/goal_readings_sample.jsonl', import.meta.url),
			'utf8'
		);
		const readings = parseGoalReadings(raw);
		assert.equal(readings.length, 3);
		const summary = summarizeGoalReadings(readings);
		const tickets = summary.get('tickets')!;
		assert.equal(tickets.latest.value, 18);
		assert.equal(tickets.previous?.value, 12);
		assert.equal(tickets.delta, 6);
		assert.equal(tickets.count, 2);
		const conversion = summary.get('conversion')!;
		assert.equal(conversion.latest.value, 0.42);
		assert.equal(conversion.count, 1);
		assert.equal(readings[0].note, 'first count');
		assert.equal(readings[1].note, null);
	});
});
