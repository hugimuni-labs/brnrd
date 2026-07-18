import assert from 'node:assert/strict';
import test from 'node:test';
import { groupByLayer, inlineTokens, markdownBlocks } from './surface.ts';

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

test('groupByLayer orders authored → knowledge → replies and defaults missing layer', () => {
	const groups = groupByLayer([
		{ path: 'knowledge/repos/x/a.md', markdown: '', layer: 'knowledge' },
		{ path: 'knowledge/replies/x/run.md', markdown: '', layer: 'replies' },
		{ path: 'surface/index.md', markdown: '' } // no layer → authored
	]);
	assert.deepEqual(
		groups.map((g) => g.layer),
		['authored', 'knowledge', 'replies']
	);
	assert.equal(groups[0].files[0].path, 'surface/index.md');
});

test('inlineTokens resolves a cross-layer relative link with home-relative paths', () => {
	const tokens = inlineTokens(
		'[kb](../knowledge/repos/x/a.md)',
		'surface/index.md',
		new Set(['knowledge/repos/x/a.md'])
	);
	assert.equal(tokens[0].kind === 'link' && tokens[0].target, 'knowledge/repos/x/a.md');
});
