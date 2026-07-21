import assert from 'node:assert/strict';
import test from 'node:test';

import { relicIcon, relicLabel } from './runLedger.ts';

test('unknown relics use the first non-empty descriptive field', () => {
	assert.equal(
		relicLabel({ kind: 'artifact', text: '', path: 'report.md', note: 'later' }),
		'report.md'
	);
	assert.equal(relicLabel({ kind: 'artifact', note: 'deployed' }), 'deployed');
	assert.equal(relicLabel({ kind: 'artifact' }), 'artifact');
	assert.equal(relicIcon('artifact'), '•');
});

test('reply relics prefer their archived content excerpt', () => {
	assert.equal(relicLabel({ kind: 'reply', excerpt: 'Shipped the fix.' }), 'Shipped the fix.');
	assert.equal(relicLabel({ kind: 'reply' }), 'reply');
});
