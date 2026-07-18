import assert from 'node:assert/strict';
import test from 'node:test';
import { frontmatterDocument, repoRunSlug, runNodeFromSurface, runNodeHref } from './runNode.ts';

test('runNodeHref maps a repo label onto the account run-directory route', () => {
	assert.equal(repoRunSlug('Gurio/brr'), 'Gurio__brr');
	assert.equal(runNodeHref('Gurio/brr', 'run-1'), '/runs/Gurio__brr/run-1');
	assert.equal(runNodeHref(null, 'run local'), '/runs/local/run%20local');
});

test('frontmatterDocument keeps metadata separate from rendered message prose', () => {
	assert.deepEqual(frontmatterDocument('---\nstatus: delivered\nkind: terminal\n---\n\nDone.'), {
		metadata: { status: 'delivered', kind: 'terminal' },
		body: 'Done.'
	});
});

test('runNodeFromSurface welds state, body, and chronologically sorted messages', () => {
	const node = runNodeFromSurface(
		{
			generated_at: 'now',
			reported_at: 'now',
			files: [
				{ path: 'runs/Gurio__brr/run-1/messages/000010-terminal.md', markdown: 'ten', layer: 'runs' },
				{ path: 'runs/Gurio__brr/run-1/state.md', markdown: '# State', layer: 'runs' },
				{ path: 'runs/Gurio__brr/run-2/body.md', markdown: 'other run', layer: 'runs' },
				{ path: 'runs/Gurio__brr/run-1/messages/000002-interim.md', markdown: 'two', layer: 'runs' },
				{ path: 'runs/Gurio__brr/run-1/body.md', markdown: '## Now', layer: 'runs' }
			]
		},
		'Gurio__brr',
		'run-1'
	);
	assert.equal(node.state?.path, 'runs/Gurio__brr/run-1/state.md');
	assert.equal(node.body?.path, 'runs/Gurio__brr/run-1/body.md');
	assert.deepEqual(node.messages.map((message) => message.file.path.split('/').at(-1)), [
		'000002-interim.md',
		'000010-terminal.md'
	]);
});
