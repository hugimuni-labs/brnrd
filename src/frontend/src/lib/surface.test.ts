import assert from 'node:assert/strict';
import test from 'node:test';
import { inlineTokens, markdownBlocks } from './surface.ts';

test('markdownBlocks keeps authored structure as data', () => {
	assert.deepEqual(markdownBlocks('# Work\n\n- one\n- two\n\n```\n<x>\n```'), [
		{ kind: 'heading', level: 1, text: 'Work' },
		{ kind: 'list', ordered: false, items: ['one', 'two'] },
		{ kind: 'code', text: '<x>' }
	]);
});

test('inlineTokens resolves known relative pages and refuses script links', () => {
	const tokens = inlineTokens(
		'[plan](plans/repo/active.md) [bad](javascript:alert)',
		'index.md',
		new Set(['plans/repo/active.md'])
	);
	assert.equal(tokens[0].kind === 'link' && tokens[0].target, 'plans/repo/active.md');
	assert.equal(tokens[2].kind === 'link' && tokens[2].href, null);
});
