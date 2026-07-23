import assert from 'node:assert/strict';
import test from 'node:test';
import {
	bodySection,
	dispatchEdges,
	frameFields,
	frontmatterDocument,
	messageInstant,
	messageTarget,
	messageTone,
	nodeDigest,
	nowProjection,
	repoRunSlug,
	runLedgerRowsForNode,
	runIdSlug,
	runNodeFromSurface,
	runNodeHref,
	runNodeHrefForPath
} from './runNode.ts';
import type { SurfaceResponse } from './surface.ts';

function surface(files: SurfaceResponse['files']): SurfaceResponse {
	return { generated_at: 'now', reported_at: 'now', files };
}

// The href has to reproduce the directory name `account.run_dir` wrote. These
// pin it against the Python side's `_slug` (src/brr/account.py), which the
// handoff sketch mismatched on both the separator set and the empty fallback.
test('repoRunSlug mirrors the account slug, including its "home" fallback', () => {
	assert.equal(repoRunSlug('Gurio/brr'), 'Gurio__brr');
	assert.equal(repoRunSlug('acme corp/some repo'), 'acme-corp__some-repo');
	assert.equal(repoRunSlug(null), 'home');
	assert.equal(repoRunSlug('  '), 'home');
	assert.equal(repoRunSlug('///'), 'home');
});

test('runIdSlug mirrors run_dir sanitization, including its "unknown-run" fallback', () => {
	assert.equal(runIdSlug('run-260718-2215-k8dy'), 'run-260718-2215-k8dy');
	assert.equal(runIdSlug('run local'), 'run-local');
	assert.equal(runIdSlug(''), 'unknown-run');
});

test('runNodeHref routes onto the account run directory', () => {
	assert.equal(runNodeHref('Gurio/brr', 'run-1'), '/runs/Gurio__brr/run-1');
	assert.equal(runNodeHref(null, 'run local'), '/runs/home/run-local');
});

test('runNodeHrefForPath turns a corpus run path into an edge, and ignores the rest', () => {
	assert.equal(
		runNodeHrefForPath('runs/Gurio__brr/run-1/messages/000001-terminal.md'),
		'/runs/Gurio__brr/run-1'
	);
	assert.equal(runNodeHrefForPath('runs/Gurio__brr/run-1/state.md'), '/runs/Gurio__brr/run-1');
	assert.equal(runNodeHrefForPath('knowledge/repos/Gurio__brr/design.md'), null);
	assert.equal(runNodeHrefForPath('runs/Gurio__brr'), null);
	assert.equal(runNodeHrefForPath('surface/index.md'), null);
});

test('runLedgerRowsForNode matches both repo and run directory slugs', () => {
	const row = (repo_label: string, run_id: string) =>
		({ repo_label, run_id }) as Parameters<typeof runLedgerRowsForNode>[0][number];
	const rows = [
		row('Gurio/brr', 'run shared'),
		row('Other/repo', 'run shared'),
		row('Gurio/brr', 'run-other')
	];
	assert.deepEqual(runLedgerRowsForNode(rows, 'Gurio__brr', 'run-shared'), [rows[0]]);
});

test('frontmatterDocument keeps metadata separate from rendered prose', () => {
	assert.deepEqual(frontmatterDocument('---\nstatus: delivered\nkind: terminal\n---\n\nDone.'), {
		metadata: { status: 'delivered', kind: 'terminal' },
		body: 'Done.'
	});
});

test('frontmatterDocument keeps colons inside a value, and tolerates no header', () => {
	const doc = frontmatterDocument('---\ntarget_thread: telegram:155783668:\n---\n\nbody\n');
	assert.equal(doc.metadata.target_thread, 'telegram:155783668:');
	assert.deepEqual(frontmatterDocument('# Plain page\n').metadata, {});
	assert.equal(frontmatterDocument('# Plain page\n').body, '# Plain page');
	// An unterminated header is prose, not silently swallowed metadata.
	assert.equal(frontmatterDocument('---\nstatus: pending\n').body, '---\nstatus: pending');
});

test('runNodeFromSurface welds state, body, and messages in write order', () => {
	const node = runNodeFromSurface(
		surface([
			{
				path: 'runs/Gurio__brr/run-1/messages/000010-terminal.md',
				markdown: 'ten',
				layer: 'runs'
			},
			{ path: 'runs/Gurio__brr/run-1/state.md', markdown: '# State', layer: 'runs' },
			{ path: 'runs/Gurio__brr/run-2/body.md', markdown: 'other run', layer: 'runs' },
			{ path: 'runs/Gurio__brr/run-1/messages/000002-interim.md', markdown: 'two', layer: 'runs' },
			{ path: 'runs/Gurio__brr/run-1/body.md', markdown: '## Now', layer: 'runs' }
		]),
		'Gurio__brr',
		'run-1'
	);
	assert.equal(node.mirrored, true);
	assert.equal(node.state?.path, 'runs/Gurio__brr/run-1/state.md');
	assert.equal(node.body?.path, 'runs/Gurio__brr/run-1/body.md');
	assert.deepEqual(
		node.messages.map((message) => message.file.path.split('/').at(-1)),
		['000002-interim.md', '000010-terminal.md']
	);
});

// `runs/<slug>/run-1` must not swallow `run-10`'s files: the prefix is only a
// node boundary when it ends at the separator.
test('runNodeFromSurface does not bleed across run ids sharing a prefix', () => {
	const node = runNodeFromSurface(
		surface([
			{ path: 'runs/Gurio__brr/run-1/state.md', markdown: 'mine', layer: 'runs' },
			{ path: 'runs/Gurio__brr/run-10/state.md', markdown: 'not mine', layer: 'runs' },
			{ path: 'runs/Gurio__brr/run-10/body.md', markdown: 'not mine', layer: 'runs' }
		]),
		'Gurio__brr',
		'run-1'
	);
	assert.equal(node.state?.markdown, 'mine');
	assert.equal(node.messages.length, 0);
	assert.equal(node.body, null);
});

// Pre-weld and partial nodes are normal, and each must be distinguishable: a
// node absent from the mirror is a different statement than a node that wrote
// no body, and the page says something different for each.
test('runNodeFromSurface reports an absent node distinctly from a partial one', () => {
	const absent = runNodeFromSurface(surface([]), 'Gurio__brr', 'run-1');
	assert.equal(absent.mirrored, false);
	assert.equal(absent.state, null);
	assert.equal(absent.body, null);

	const partial = runNodeFromSurface(
		surface([{ path: 'runs/Gurio__brr/run-1/state.md', markdown: '# State', layer: 'runs' }]),
		'Gurio__brr',
		'run-1'
	);
	assert.equal(partial.mirrored, true);
	assert.equal(partial.body, null);
	assert.deepEqual(partial.messages, []);
});

// The runs layer is the only one that can carry a node; a knowledge page that
// happens to sit at a colliding path is not run traffic.
test('runNodeFromSurface only accepts files tagged as the runs layer', () => {
	const node = runNodeFromSurface(
		surface([
			{ path: 'runs/Gurio__brr/run-1/state.md', markdown: 'x', layer: 'knowledge' },
			{ path: 'runs/Gurio__brr/run-1/body.md', markdown: 'x' }
		]),
		'Gurio__brr',
		'run-1'
	);
	assert.equal(node.mirrored, false);
});

test('frameFields orders the attested frame and drops empty or duplicated keys', () => {
	const fields = frameFields({
		run_id: 'run-1',
		repo_label: 'Gurio/brr',
		status: 'done',
		stage: 'closeout',
		runner_name: 'claude-opus',
		target_branch: '',
		pid: '4242',
		some_new_key: 'kept'
	});
	assert.deepEqual(fields, [
		{ label: 'status', value: 'done' },
		{ label: 'stage', value: 'closeout' },
		{ label: 'runner', value: 'claude-opus' },
		{ label: 'some_new_key', value: 'kept' }
	]);
});

test('messageTone recognises exactly the statuses message_store writes', () => {
	assert.equal(messageTone('delivered'), 'delivered');
	assert.equal(messageTone('pending'), 'pending');
	assert.equal(messageTone('undeliverable'), 'undeliverable');
	assert.equal(messageTone('collected'), 'collected');
	assert.equal(messageTone('failed'), 'unknown');
	assert.equal(messageTone(undefined), 'unknown');
});

test('messageTarget and messageInstant read the frontmatter the store actually writes', () => {
	assert.equal(messageTarget({ target_event: 'evt-1' }), 'event evt-1');
	assert.equal(messageTarget({ target_gate: 'forge' }), 'gate forge');
	assert.equal(messageTarget({ target_thread: 'telegram:1' }), 'telegram:1');
	assert.equal(messageTarget({}), '');
	assert.equal(messageInstant({ created_at: 'a', delivered_at: 'b' }), 'b');
	assert.equal(messageInstant({ created_at: 'a' }), 'a');
	assert.equal(messageInstant({}), '');
});

test('dispatch edges link only to neighbours the corpus actually mirrors', () => {
	const mirrored = new Set(['runs/Gurio__brr/run-parent/state.md']);
	const edges = dispatchEdges(
		{
			source: 'spawn',
			parent_run_id: 'run-parent',
			child_run_ids: 'run-parent, run-ghost'
		},
		'Gurio__brr',
		mirrored
	);

	assert.equal(edges.origin, '');
	assert.deepEqual(edges.parent, { runId: 'run-parent', href: '/runs/Gurio__brr/run-parent' });
	// A real edge whose node is not in this snapshot is named, not linked.
	assert.deepEqual(edges.children, [
		{ runId: 'run-parent', href: '/runs/Gurio__brr/run-parent' },
		{ runId: 'run-ghost', href: null }
	]);
});

test('a run with no parent names its non-run dispatcher instead', () => {
	assert.equal(
		dispatchEdges({ source: 'schedule' }, 'Gurio__brr', new Set()).origin,
		'a scheduled wake'
	);
	assert.equal(
		dispatchEdges(
			{ source: 'telegram', conversation_key: 'telegram:155783668:' },
			'Gurio__brr',
			new Set()
		).origin,
		'telegram:155783668:'
	);
	assert.equal(
		dispatchEdges({ source: 'github' }, 'Gurio__brr', new Set()).origin,
		'the github thread'
	);
	// Nothing recorded stays nothing — no invented origin.
	const bare = dispatchEdges({}, 'Gurio__brr', new Set());
	assert.equal(bare.origin, '');
	assert.equal(bare.parent, null);
	assert.deepEqual(bare.children, []);
});

test('edge fields are lifted out of the generic frame list, not duplicated', () => {
	const labels = frameFields({
		status: 'done',
		parent_run_id: 'run-parent',
		child_run_ids: 'run-child'
	}).map((field) => field.label);

	assert.deepEqual(labels, ['status']);
});

test('nowProjection mirrors the daemon card projection, including its fallback', () => {
	assert.equal(
		nowProjection('## Now\n\nDriving tests.\n\n## Arc\n\nA long permanent story.'),
		'Driving tests.'
	);
	// A body with no sections is entirely the now — legacy one-note cards.
	assert.equal(nowProjection('Plain legacy note'), 'Plain legacy note');
	assert.equal(nowProjection(''), '');
	assert.equal(nowProjection('## NOW\nx\n'), 'x');
	// A double-spaced heading is not a match — on this side or the Python one
	// (`line.strip().casefold() == "## now"` leaves the inner space too), so
	// the whole body is the now. Pinned because the two must agree even here.
	assert.equal(nowProjection('##  now\nx'), '##  now\nx');
});

test('nodeDigest offers the expand only when expanding reveals something', () => {
	const node = (files: SurfaceResponse['files']) =>
		nodeDigest(runNodeFromSurface(surface(files), 'Gurio__brr', 'run-1'));

	const sectioned = node([
		{
			path: 'runs/Gurio__brr/run-1/state.md',
			markdown: '---\nstatus: running\nstage: running\nrunner_name: claude-opus\n---\n',
			layer: 'runs'
		},
		{ path: 'runs/Gurio__brr/run-1/body.md', markdown: '## Now\n\nx\n\n## Arc\n\ny', layer: 'runs' }
	]);
	assert.equal(sectioned.now, 'x');
	assert.equal(sectioned.status, 'running');
	assert.equal(sectioned.runner, 'claude-opus');
	assert.equal(sectioned.hasMore, true);

	// A one-section body with no traffic has nothing behind the expand.
	const flat = node([
		{ path: 'runs/Gurio__brr/run-1/state.md', markdown: '---\nstatus: done\n---\n', layer: 'runs' },
		{ path: 'runs/Gurio__brr/run-1/body.md', markdown: '## Now\n\nonly this', layer: 'runs' }
	]);
	assert.equal(flat.hasMore, false);
	assert.equal(flat.messageCount, 0);

	// An unmirrored node reports itself rather than rendering empty vitals.
	assert.equal(node([]).mirrored, false);
});

test('bodySection lifts the produce manifest off the attested frame', () => {
	// The node's produce arrives as Markdown in `state.md` rather than as a
	// parallel JSON schema — the relic vocabulary was already hand-mirrored in
	// two places and a third copy was not worth a section of links.
	const frame = [
		'# Run run-1',
		'',
		'- status: running',
		'',
		'## Request',
		'',
		'do the thing',
		'',
		'## Produce',
		'',
		'- 🔨 [abc1234 do the thing](https://forge/commit/abc1234)',
		'- 🔀 [PR #487](https://forge/pr/487)'
	].join('\n');
	assert.equal(
		bodySection(frame, 'Produce'),
		'- 🔨 [abc1234 do the thing](https://forge/commit/abc1234)\n- 🔀 [PR #487](https://forge/pr/487)'
	);
	assert.equal(bodySection(frame, 'Request'), 'do the thing');
	// A node written before produce was recorded on the frame reads empty —
	// which the renderer must not flatten into "produced nothing".
	assert.equal(bodySection('# Run run-1\n\n- status: done', 'Produce'), '');
});

test('nodeDigest carries the frame mood as a bare handle (#566)', () => {
	const node = (frontmatter: string) =>
		nodeDigest(
			runNodeFromSurface(
				surface([
					{
						path: 'runs/Gurio__brr/run-1/state.md',
						markdown: `---\n${frontmatter}---\n`,
						layer: 'runs'
					}
				]),
				'Gurio__brr',
				'run-1'
			)
		);

	// A closed run's frame is a text record: the handle survives, the glyph
	// never existed there. The chip renders the bare name, which is exactly
	// what the emote library's honesty bar asks for.
	assert.equal(node('status: done\nmood: id_l\n').mood, 'id_l');
	// A run that set no mood reports none — and '' renders nothing at all.
	assert.equal(node('status: done\n').mood, '');
});
