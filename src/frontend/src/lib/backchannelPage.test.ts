import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseBackchannelPage, parseRefs } from './backchannelPage.ts';

test('empty page parses to no items', () => {
	assert.deepEqual(parseBackchannelPage(''), []);
	assert.deepEqual(parseBackchannelPage('\n\n  \n'), []);
});

test('a page with prose but no `## ` section still parses to no items', () => {
	const markdown = `# Backchannel — what waits on you

Some intro prose that never opens a section.
`;
	assert.deepEqual(parseBackchannelPage(markdown), []);
});

test('an item with no rows keeps its heading and treats everything after it as body', () => {
	const markdown = `## Just a heading, no rows

Only free markdown here, nothing recognized.
`;
	const items = parseBackchannelPage(markdown);
	assert.equal(items.length, 1);
	assert.equal(items[0].headline, 'Just a heading, no rows');
	assert.equal(items[0].kind, null);
	assert.deepEqual(items[0].refs, []);
	assert.equal(items[0].prompt, null);
	assert.equal(items[0].bodyMarkdown, 'Only free markdown here, nothing recognized.');
});

test('unknown rows are not schema — they stay in the body as plain prose', () => {
	const markdown = `## An item with a row nobody defined

owner: alice
severity: high

The body starts at the first unrecognized line and keeps everything after it,
rows included.
`;
	const items = parseBackchannelPage(markdown);
	assert.equal(items.length, 1);
	assert.equal(items[0].kind, null);
	assert.deepEqual(items[0].refs, []);
	assert.equal(items[0].prompt, null);
	assert.match(items[0].bodyMarkdown, /^owner: alice\nseverity: high\n\n/);
	assert.match(items[0].bodyMarkdown, /rows included\.$/);
});

test('an unrecognized kind value is dropped, not leaked into the body', () => {
	const markdown = `## Something urgent

kind: urgent
refs: [#1](https://example.test/1)

Body text.
`;
	const items = parseBackchannelPage(markdown);
	assert.equal(items[0].kind, null);
	assert.deepEqual(items[0].refs, [{ label: '#1', href: 'https://example.test/1' }]);
	assert.equal(items[0].bodyMarkdown, 'Body text.');
});

test('refs mix bracketed issue/PR links and bare kb-page-name labels', () => {
	assert.deepEqual(
		parseRefs('[#870](https://github.com/hugimuni-labs/brnrd/pull/870) · decision-mcp-stance.md'),
		[
			{ label: '#870', href: 'https://github.com/hugimuni-labs/brnrd/pull/870' },
			{ label: 'decision-mcp-stance.md', href: null }
		]
	);
	assert.deepEqual(parseRefs('workflow.md §Gating and merges'), [
		{ label: 'workflow.md §Gating and merges', href: null }
	]);
	assert.deepEqual(parseRefs('docs/legal/art-30-record.md · legal/export/open-facts.md'), [
		{ label: 'docs/legal/art-30-record.md', href: null },
		{ label: 'legal/export/open-facts.md', href: null }
	]);
});

test('the qualified forge shorthand owner/repo#N resolves; a bare #N never does', () => {
	// The multi-repo ref grammar: qualified is deterministic, so it links.
	assert.deepEqual(parseRefs('hugimuni-labs/brnrd#928 · other-org/site#3'), [
		{ label: 'hugimuni-labs/brnrd#928', href: 'https://github.com/hugimuni-labs/brnrd/issues/928' },
		{ label: 'other-org/site#3', href: 'https://github.com/other-org/site/issues/3' }
	]);
	// A bare #N names no repo on an account-global surface: ambiguity renders
	// as ambiguity — plain text — never as a guessed link.
	assert.deepEqual(parseRefs('#928'), [{ label: '#928', href: null }]);
	// Near-misses stay labels: no number, path-ish slashes, stray spaces.
	assert.deepEqual(parseRefs('hugimuni-labs/brnrd#'), [
		{ label: 'hugimuni-labs/brnrd#', href: null }
	]);
	assert.deepEqual(parseRefs('a/b/c#1'), [{ label: 'a/b/c#1', href: null }]);
});

test('a full item carries kind, refs, prompt, and body together', () => {
	const markdown = `## #853 — the MCP direction: inherit or isolate

kind: decide
refs: [#870](https://github.com/hugimuni-labs/brnrd/pull/870) · decision-mcp-stance.md
prompt: The MCP direction is decided: <inherit|isolate>. Fold it into decision-mcp-stance.md.

\`decision-mcp-stance.md\` says *opt-in*; your recorded ask was *default-on*.
`;
	const items = parseBackchannelPage(markdown);
	assert.equal(items.length, 1);
	const [item] = items;
	assert.equal(item.headline, '#853 — the MCP direction: inherit or isolate');
	assert.equal(item.kind, 'decide');
	assert.deepEqual(item.refs, [
		{ label: '#870', href: 'https://github.com/hugimuni-labs/brnrd/pull/870' },
		{ label: 'decision-mcp-stance.md', href: null }
	]);
	assert.equal(
		item.prompt,
		'The MCP direction is decided: <inherit|isolate>. Fold it into decision-mcp-stance.md.'
	);
	assert.match(item.bodyMarkdown, /^`decision-mcp-stance\.md` says/);
});

test('document order is preserved and preamble before the first heading is dropped', () => {
	const markdown = `# Backchannel — what waits on you

Intro prose, item grammar note, none of it a section.

## First — act now

kind: act

Do the first thing.

## Second — decide

kind: decide
prompt: pick one

Do the second thing.

## Third — no rows at all

Just prose.
`;
	const items = parseBackchannelPage(markdown);
	assert.deepEqual(
		items.map((item) => item.headline),
		['First — act now', 'Second — decide', 'Third — no rows at all']
	);
	assert.equal(items[0].kind, 'act');
	assert.equal(items[1].prompt, 'pick one');
	assert.equal(items[2].kind, null);
	// Keys stay ordered and distinct even though nothing in the grammar
	// assigns an explicit id.
	assert.deepEqual(
		items.map((item) => item.key),
		items.map((item) => item.key)
	);
	assert.equal(new Set(items.map((item) => item.key)).size, 3);
});

test('an item with only some rows leaves the others at their empty default', () => {
	const markdown = `## GitHub App permission trim, while a tab is open

kind: act

Administration: **read** · Dependabot alerts: **read**.
`;
	const items = parseBackchannelPage(markdown);
	assert.equal(items[0].kind, 'act');
	assert.deepEqual(items[0].refs, []);
	assert.equal(items[0].prompt, null);
});

test('a heading with no blank line before its rows still parses', () => {
	const markdown = `## Tight formatting
kind: read
refs: some-page.md

Body.
`;
	const items = parseBackchannelPage(markdown);
	assert.equal(items[0].kind, 'read');
	assert.deepEqual(items[0].refs, [{ label: 'some-page.md', href: null }]);
	assert.equal(items[0].bodyMarkdown, 'Body.');
});
