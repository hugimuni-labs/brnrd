import assert from 'node:assert/strict';
import test from 'node:test';

import { shelfEntries } from './shelfPages.ts';
import type { SurfaceFile } from './surface.ts';

// The shelf's own rule (surface/shelf/index.md §Expiry is rendered, never
// swept): a page past its `keeps:` still shows, struck, with what ended it.
// These tests pin the two frontmatter shapes actually on disk (the
// documented `made-for:`/`asked:` block, and the looser `made:`/`keeps:`
// lines the newer pages use) and the two expiry triggers this reader can
// safely evaluate without guessing at free-text conditions.

const NOW = Date.parse('2026-09-06T00:00:00Z');

function file(path: string, markdown: string): SurfaceFile {
	return { path, markdown, layer: 'authored' };
}

test('index.md is excluded — the contract page is not a commissioned artifact', () => {
	const entries = shelfEntries(
		[file('surface/shelf/index.md', '# The shelf\n\nkeeps: n/a\n')],
		NOW
	);
	assert.equal(entries.length, 0);
});

test('a file outside surface/shelf/ is ignored even if it declares keeps:', () => {
	const entries = shelfEntries(
		[file('surface/workflow.md', '# Workflow\n\nkeeps: forever\n')],
		NOW
	);
	assert.equal(entries.length, 0);
});

test('the looser shape (title first, bare made:/keeps: lines) parses', () => {
	const entries = shelfEntries(
		[
			file(
				'surface/shelf/stars-plan-2026-09-06.md',
				'# GitHub stars — the plan, measured from zero\n\n' +
					'keeps: until the repo has a description and topics\n' +
					'made: 2026-09-06 02:20Z, run-260906-0037-b09r, on his ask\n\n' +
					'## The measurement\n'
			)
		],
		NOW
	);
	assert.equal(entries.length, 1);
	assert.equal(entries[0].title, 'GitHub stars — the plan, measured from zero');
	assert.equal(entries[0].madeLine, '2026-09-06 02:20Z, run-260906-0037-b09r, on his ask');
	assert.equal(entries[0].keepsLine, 'until the repo has a description and topics');
	assert.equal(entries[0].expired, false);
});

test('the documented shape (made-for:/asked: block before the title) parses too', () => {
	const entries = shelfEntries(
		[
			file(
				'surface/shelf/gauge-bench-heights-2026-08-19.md',
				'made-for: arseni\n' +
					'asked: evt-1787155584839918000-q8ro\n' +
					'keeps: until the provider-row gauge deck changes shape again\n' +
					'topics: the-workshop the-clockwork\n\n' +
					"# The provider-row gauge's fixed height\n"
			)
		],
		NOW
	);
	assert.equal(entries.length, 1);
	assert.equal(entries[0].title, "The provider-row gauge's fixed height");
	assert.equal(entries[0].madeLine, 'arseni · evt-1787155584839918000-q8ro');
	assert.equal(entries[0].expired, false);
});

test('a keeps: line naming "expired" marks the page expired without date math', () => {
	const entries = shelfEntries(
		[
			file(
				'surface/shelf/rail-heights-2026-08-19.md',
				'made-for: arseni\n' +
					'asked: evt-1787140446318045000-tout\n' +
					'keeps: expired 2026-08-19 — the gauge/bench split landed\n\n' +
					"# ~~The rail's own height~~\n"
			)
		],
		NOW
	);
	assert.equal(entries.length, 1);
	assert.equal(entries[0].expired, true);
	assert.equal(entries[0].title, "The rail's own height");
});

test('a keeps: date in the past expires even with no "expired" wording', () => {
	const entries = shelfEntries(
		[file('surface/shelf/old.md', '# Old page\n\nkeeps: 2026-01-01\n')],
		NOW
	);
	assert.equal(entries[0].expired, true);
});

test('a keeps: date in the future stays open', () => {
	const entries = shelfEntries(
		[file('surface/shelf/future.md', '# Future page\n\nkeeps: 2027-01-01\n')],
		NOW
	);
	assert.equal(entries[0].expired, false);
});

test('a free-text condition the reader cannot evaluate stays open, not guessed shut', () => {
	const entries = shelfEntries(
		[
			file(
				'surface/shelf/karma-recon-2026-09-06.md',
				'# Karma recon — live threads to comment on, 2026-09-06\n\n' +
					'keeps: 48 h — threads die. Measured 2026-09-05T22:2x–22:5xZ\n'
			)
		],
		NOW
	);
	assert.equal(entries[0].expired, false);
	assert.equal(entries[0].madeLine, null);
});

test('a page missing keeps: entirely is still shown, not silently dropped', () => {
	const entries = shelfEntries([file('surface/shelf/no-keeps.md', '# No keeps declared\n')], NOW);
	assert.equal(entries.length, 1);
	assert.equal(entries[0].keepsLine, null);
	assert.equal(entries[0].expired, false);
});

test('open pages sort before expired ones; each bucket sorts newest-dated first', () => {
	const entries = shelfEntries(
		[
			file('surface/shelf/a.md', '# A\n\nmade: 2026-09-01\nkeeps: 2027-01-01\n'),
			file('surface/shelf/b.md', '# B\n\nmade: 2026-09-05\nkeeps: 2027-01-01\n'),
			file('surface/shelf/c.md', '# C\n\nkeeps: expired 2026-08-01 — done\n')
		],
		NOW
	);
	assert.deepEqual(
		entries.map((e) => e.title),
		['B', 'A', 'C']
	);
});
