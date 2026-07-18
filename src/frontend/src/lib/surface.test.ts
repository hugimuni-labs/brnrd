import assert from 'node:assert/strict';
import test from 'node:test';
import {
	basename,
	buildNavTree,
	fileDirKey,
	groupByLayer,
	headingAnchor,
	inlineTokens,
	markdownBlocks,
	splitIntoSections,
	SECTION_THRESHOLD
} from './surface.ts';

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

// ── New: nav tree ─────────────────────────────────────────────────────────────

test('basename strips directory prefix', () => {
	assert.equal(basename('knowledge/repos/Gurio__brr/design.md'), 'design.md');
	assert.equal(basename('index.md'), 'index.md');
	assert.equal(basename('a/b/c.md'), 'c.md');
});

test('buildNavTree groups knowledge files by repos/<slug> dir', () => {
	const tree = buildNavTree([
		{ path: 'knowledge/repos/A__b/one.md', markdown: '', layer: 'knowledge' },
		{ path: 'knowledge/repos/A__b/two.md', markdown: '', layer: 'knowledge' },
		{ path: 'knowledge/repos/C__d/three.md', markdown: '', layer: 'knowledge' }
	]);
	assert.equal(tree.length, 1);
	const [kl] = tree;
	assert.equal(kl.layer, 'knowledge');
	assert.equal(kl.count, 3);
	assert.ok(kl.dirs !== null);
	assert.equal(kl.dirs!.length, 2);
	assert.equal(kl.dirs![0].key, 'repos/A__b');
	assert.equal(kl.dirs![0].count, 2);
	assert.equal(kl.dirs![1].key, 'repos/C__d');
	assert.equal(kl.dirs![1].count, 1);
});

test('buildNavTree groups replies files by slug dir', () => {
	const tree = buildNavTree([
		{ path: 'knowledge/replies/Gurio__brr/run-1.md', markdown: '', layer: 'replies' },
		{ path: 'knowledge/replies/Gurio__brr/run-2.md', markdown: '', layer: 'replies' },
		{ path: 'knowledge/replies/Other__repo/run-3.md', markdown: '', layer: 'replies' }
	]);
	const [rl] = tree;
	assert.equal(rl.layer, 'replies');
	assert.ok(rl.dirs !== null);
	assert.equal(rl.dirs![0].key, 'Gurio__brr');
	assert.equal(rl.dirs![0].count, 2);
	assert.equal(rl.dirs![1].key, 'Other__repo');
});

test('buildNavTree keeps authored layer flat (no dirs)', () => {
	const tree = buildNavTree([
		{ path: 'surface/index.md', markdown: '' },
		{ path: 'surface/work.md', markdown: '' }
	]);
	const [al] = tree;
	assert.equal(al.layer, 'authored');
	assert.equal(al.dirs, null);
	assert.equal(al.flatFiles.length, 2);
});

test('fileDirKey returns correct ancestor key for ancestor auto-expansion', () => {
	assert.equal(fileDirKey('knowledge/repos/Gurio__brr/design.md', 'knowledge'), 'repos/Gurio__brr');
	assert.equal(fileDirKey('knowledge/replies/Gurio__brr/run-1.md', 'replies'), 'Gurio__brr');
	assert.equal(fileDirKey('knowledge/_cross-repo/shared.md', 'knowledge'), '_cross-repo');
	assert.equal(fileDirKey('surface/index.md', 'authored'), null);
});

// ── New: outline reader ───────────────────────────────────────────────────────

test('splitIntoSections returns null for short pages', () => {
	const blocks = Array.from({ length: SECTION_THRESHOLD }, (_, i) => ({
		kind: 'paragraph' as const,
		text: `p${i}`
	}));
	assert.equal(splitIntoSections(blocks), null);
});

test('splitIntoSections splits long pages on h2', () => {
	const blocks = [
		{ kind: 'heading' as const, level: 2, text: 'Alpha' },
		...Array.from({ length: 8 }, (_, i) => ({ kind: 'paragraph' as const, text: `a${i}` })),
		{ kind: 'heading' as const, level: 2, text: 'Beta' },
		...Array.from({ length: 8 }, (_, i) => ({ kind: 'paragraph' as const, text: `b${i}` }))
	];
	const sections = splitIntoSections(blocks);
	assert.ok(sections !== null);
	assert.equal(sections!.length, 2);
	assert.equal(sections![0].heading?.text, 'Alpha');
	assert.equal(sections![0].preview?.kind, 'paragraph');
	assert.equal(sections![0].tail.length, 7); // 8 body blocks - 1 preview
	assert.equal(sections![1].heading?.text, 'Beta');
});

test('splitIntoSections falls back to h3 when no h2 exists', () => {
	const blocks = [
		{ kind: 'heading' as const, level: 3, text: 'One' },
		...Array.from({ length: 8 }, (_, i) => ({ kind: 'paragraph' as const, text: `x${i}` })),
		{ kind: 'heading' as const, level: 3, text: 'Two' },
		...Array.from({ length: 8 }, (_, i) => ({ kind: 'paragraph' as const, text: `y${i}` }))
	];
	const sections = splitIntoSections(blocks);
	assert.ok(sections !== null);
	assert.equal(sections!.length, 2);
	assert.equal(sections![0].heading?.text, 'One');
});

test('splitIntoSections returns null when no split points exist at chosen level', () => {
	// Long page but all headings are h4 — no h2 or h3 to split on, only one section.
	const blocks = [
		{ kind: 'heading' as const, level: 4, text: 'Deep' },
		...Array.from({ length: 20 }, (_, i) => ({ kind: 'paragraph' as const, text: `p${i}` }))
	];
	assert.equal(splitIntoSections(blocks), null);
});

test('headingAnchor produces GitHub-style slug', () => {
	assert.equal(headingAnchor('The Corpus Join'), 'the-corpus-join');
	assert.equal(headingAnchor('foo/bar (baz)'), 'foobar-baz');
	assert.equal(headingAnchor('  spaces  '), 'spaces');
});

test('inlineTokens captures fragment anchor from cross-file links', () => {
	// From surface/index.md, a cross-layer link needs ".." to reach knowledge/.
	const tokens = inlineTokens(
		'[see](../knowledge/repos/x/a.md#section-two)',
		'surface/index.md',
		new Set(['knowledge/repos/x/a.md'])
	);
	const link = tokens[0];
	assert.equal(link.kind === 'link' && link.target, 'knowledge/repos/x/a.md');
	assert.equal(link.kind === 'link' && link.anchor, 'section-two');
});

test('inlineTokens keeps same-page fragment links navigable', () => {
	const tokens = inlineTokens(
		'[jump](#section-two)',
		'knowledge/index.md',
		new Set(['knowledge/index.md'])
	);
	const link = tokens[0];
	assert.equal(link.kind === 'link' && link.target, 'knowledge/index.md');
	assert.equal(link.kind === 'link' && link.anchor, 'section-two');
});
