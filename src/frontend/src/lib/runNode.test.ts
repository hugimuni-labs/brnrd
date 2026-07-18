import assert from 'node:assert/strict';
import test from 'node:test';
import {
	frameFields,
	frontmatterDocument,
	messageInstant,
	messageTarget,
	messageTone,
	repoRunSlug,
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
