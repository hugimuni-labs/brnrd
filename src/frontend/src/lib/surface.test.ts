import assert from 'node:assert/strict';
import test from 'node:test';
import {
	basename,
	buildNavTree,
	fileDirKey,
	groupByLayer,
	headingAnchor,
	hiddenCount,
	inlineTokens,
	markdownBlocks,
	previewBlock,
	splitIntoSections,
	PREVIEW_ITEMS,
	SECTION_THRESHOLD
} from './surface.ts';

test('markdownBlocks keeps authored structure as data', () => {
	assert.deepEqual(markdownBlocks('# Work\n\n- one\n- two\n\n```\n<x>\n```'), [
		{ kind: 'heading', level: 1, text: 'Work' },
		{ kind: 'list', ordered: false, items: [{ text: 'one' }, { text: 'two' }] },
		{ kind: 'code', text: '<x>' }
	]);
});

// ── List chunking: one list stays one list ────────────────────────────────────

test('markdownBlocks folds wrapped continuation lines into their item', () => {
	// The live ranked-moves shape: bold lead, then a 3-space continuation. Before
	// this, each item became its own <ol> (all numbered 1.) with the remainder
	// orphaned as a top-level paragraph.
	const blocks = markdownBlocks(
		'1. **First** — opening clause\n   and its wrapped remainder.\n2. **Second** — another.\n'
	);
	assert.equal(blocks.length, 1);
	const list = blocks[0];
	assert.equal(list.kind, 'list');
	if (list.kind !== 'list') return;
	assert.equal(list.ordered, true);
	assert.equal(list.start, 1);
	assert.deepEqual(list.items, [
		{ text: '**First** — opening clause and its wrapped remainder.' },
		{ text: '**Second** — another.' }
	]);
});

test('markdownBlocks keeps an ordered list whole across a lazy continuation', () => {
	const blocks = markdownBlocks('1. one\nunindented wrap\n2. two\n');
	assert.equal(blocks.length, 1);
	const list = blocks[0];
	assert.ok(list.kind === 'list' && list.items.length === 2);
});

test('markdownBlocks nests a sub-list under its parent item', () => {
	const blocks = markdownBlocks('- parent\n  - kid a\n  - kid b\n- sibling\n');
	assert.equal(blocks.length, 1);
	const list = blocks[0];
	assert.ok(list.kind === 'list');
	if (list.kind !== 'list') return;
	assert.equal(list.items.length, 2);
	assert.equal(list.items[0].text, 'parent');
	const kids = list.items[0].children;
	assert.ok(kids && kids.length === 1 && kids[0].kind === 'list');
	assert.deepEqual(kids[0].kind === 'list' ? kids[0].items : null, [
		{ text: 'kid a' },
		{ text: 'kid b' }
	]);
	assert.equal(list.items[1].text, 'sibling');
});

test('markdownBlocks keeps a loose list (blank lines between items) whole', () => {
	const blocks = markdownBlocks('- one\n\n- two\n\n- three\n');
	assert.equal(blocks.length, 1);
	assert.ok(blocks[0].kind === 'list' && blocks[0].items.length === 3);
});

test('markdownBlocks records the authored start of an ordered list', () => {
	const blocks = markdownBlocks('3. third\n4. fourth\n');
	assert.ok(blocks[0].kind === 'list' && blocks[0].start === 3);
});

test('markdownBlocks does not merge a bullet list into an ordered one', () => {
	const blocks = markdownBlocks('- bullet\n1. number\n');
	assert.equal(blocks.length, 2);
	assert.ok(blocks[0].kind === 'list' && blocks[0].ordered === false);
	assert.ok(blocks[1].kind === 'list' && blocks[1].ordered === true);
});

test('markdownBlocks carries an indented code block inside its item', () => {
	const blocks = markdownBlocks('- item\n\n  ```\n  x = 1\n  ```\n\n- next\n');
	assert.equal(blocks.length, 1);
	const list = blocks[0];
	assert.ok(list.kind === 'list' && list.items.length === 2);
	if (list.kind !== 'list') return;
	assert.deepEqual(list.items[0].children, [{ kind: 'code', text: 'x = 1' }]);
});

test('inlineTokens renders backticked spans as code tokens', () => {
	const tokens = inlineTokens('run `brr status` now', 'index.md', new Set());
	assert.deepEqual(tokens, [
		{ kind: 'text', text: 'run ' },
		{ kind: 'code', text: 'brr status' },
		{ kind: 'text', text: ' now' }
	]);
});

test('previewBlock clamps a collapsed list by item and hiddenCount reports it', () => {
	const list = {
		kind: 'list' as const,
		ordered: true,
		start: 1,
		items: [{ text: 'a' }, { text: 'b' }, { text: 'c' }, { text: 'd' }]
	};
	const section = {
		heading: null,
		preview: list,
		tail: [{ kind: 'paragraph' as const, text: 'z' }]
	};
	const clamped = previewBlock(list, true);
	assert.ok(clamped.kind === 'list' && clamped.items.length === PREVIEW_ITEMS);
	// The list's own numbering survives the clamp — no restart at 1 elsewhere.
	assert.ok(clamped.kind === 'list' && clamped.start === 1);
	// Expanding restores every item, so nothing is reachable only via the clamp.
	assert.equal(previewBlock(list, false), list);
	assert.equal(hiddenCount(section), 1 + (4 - PREVIEW_ITEMS));
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

test('groupByLayer orders authored → knowledge → runs and defaults missing layer', () => {
	const groups = groupByLayer([
		{ path: 'knowledge/repos/x/a.md', markdown: '', layer: 'knowledge' },
		{ path: 'runs/x/run/body.md', markdown: '', layer: 'runs' },
		{ path: 'surface/index.md', markdown: '' } // no layer → authored
	]);
	assert.deepEqual(
		groups.map((g) => g.layer),
		['authored', 'knowledge', 'runs']
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

test('buildNavTree groups run files by run directory', () => {
	const tree = buildNavTree([
		{ path: 'runs/Gurio__brr/run-1/body.md', markdown: '', layer: 'runs' },
		{ path: 'runs/Gurio__brr/run-2/body.md', markdown: '', layer: 'runs' },
		{ path: 'runs/Other__repo/run-3/body.md', markdown: '', layer: 'runs' }
	]);
	const [rl] = tree;
	assert.equal(rl.layer, 'runs');
	assert.ok(rl.dirs !== null);
	assert.equal(rl.dirs![0].key, 'Gurio__brr/run-1');
	assert.equal(rl.dirs![0].count, 1);
	assert.equal(rl.dirs![1].key, 'Gurio__brr/run-2');
	assert.equal(rl.dirs![2].key, 'Other__repo/run-3');
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
	assert.equal(fileDirKey('runs/Gurio__brr/run-1/body.md', 'runs'), 'Gurio__brr/run-1');
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

test('splitIntoSections weighs list items, so a list-heavy page still folds', () => {
	// 6 blocks, but the lists carry 12 items — before item-weighting this page
	// rendered flat and the reader lost the fold on exactly the ranked plans the
	// outline exists for.
	const blocks = [
		{ kind: 'heading' as const, level: 2, text: 'Alpha' },
		{ kind: 'paragraph' as const, text: 'a' },
		{
			kind: 'list' as const,
			ordered: true,
			start: 1,
			items: Array.from({ length: 8 }, (_, i) => ({ text: `i${i}` }))
		},
		{ kind: 'heading' as const, level: 2, text: 'Beta' },
		{ kind: 'paragraph' as const, text: 'b' },
		{
			kind: 'list' as const,
			ordered: false,
			items: Array.from({ length: 4 }, (_, i) => ({ text: `j${i}` }))
		}
	];
	const sections = splitIntoSections(blocks);
	assert.ok(sections !== null);
	assert.equal(sections!.length, 2);
	assert.equal(sections![0].preview?.kind, 'paragraph');
	assert.equal(sections![0].tail.length, 1); // the whole list, unsplit
	assert.equal(sections![0].tail[0].kind, 'list');
});
