/**
 * Tests for the minimal Markdown renderer (renderMarkdown.ts).
 *
 * Covers the subset of Markdown that appears in warp item prose bodies:
 * headings, bold, italic, inline code, lists, paragraphs, and the
 * section-splitting `renderWarpBody` helper.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { renderMarkdown, renderWarpBody } from './renderMarkdown.ts';

// ── renderMarkdown ────────────────────────────────────────────────────────────

describe('renderMarkdown', () => {
	it('renders a plain paragraph', () => {
		const html = renderMarkdown('Hello world.');
		assert.equal(html, '<p>Hello world.</p>');
	});

	it('renders bold text', () => {
		const html = renderMarkdown('**State:** done.');
		assert.ok(html.includes('<strong>State:</strong>'), `got: ${html}`);
	});

	it('renders italic text', () => {
		const html = renderMarkdown('*note*');
		assert.ok(html.includes('<em>note</em>'), `got: ${html}`);
	});

	it('renders inline code', () => {
		const html = renderMarkdown('run `brnrd item stamp w-1`');
		assert.ok(html.includes('<code>brnrd item stamp w-1</code>'), `got: ${html}`);
	});

	it('does not bold inside inline code', () => {
		const html = renderMarkdown('`**not bold**`');
		// The ** should be inside a <code> tag, not converted to <strong>.
		assert.ok(html.includes('<code>'), `got: ${html}`);
		assert.ok(!html.includes('<strong>'), `got: ${html}`);
	});

	it('escapes HTML entities in text', () => {
		const html = renderMarkdown('a < b & c > d');
		assert.ok(html.includes('&lt;'), `got: ${html}`);
		assert.ok(html.includes('&amp;'), `got: ${html}`);
		assert.ok(html.includes('&gt;'), `got: ${html}`);
		assert.ok(!html.includes('<b'), `got: ${html}`);
	});

	it('renders an unordered list', () => {
		const html = renderMarkdown('- alpha\n- beta\n- gamma');
		assert.ok(html.includes('<ul>'), `got: ${html}`);
		assert.ok(html.includes('<li>alpha</li>'), `got: ${html}`);
		assert.ok(html.includes('<li>beta</li>'), `got: ${html}`);
	});

	it('renders a heading', () => {
		const html = renderMarkdown('## Answer');
		assert.ok(html.includes('<h2>Answer</h2>'), `got: ${html}`);
	});

	it('renders h3 for ###', () => {
		const html = renderMarkdown('### Sub-section');
		assert.ok(html.includes('<h3>Sub-section</h3>'), `got: ${html}`);
	});

	it('returns empty string for empty input', () => {
		assert.equal(renderMarkdown(''), '');
		assert.equal(renderMarkdown('   '), '');
	});

	it('renders a horizontal rule', () => {
		const html = renderMarkdown('---');
		assert.ok(html.includes('<hr>'), `got: ${html}`);
	});

	it('renders multiple paragraphs', () => {
		const md = 'First paragraph.\n\nSecond paragraph.';
		const html = renderMarkdown(md);
		assert.ok(html.includes('<p>First paragraph.</p>'), `got: ${html}`);
		assert.ok(html.includes('<p>Second paragraph.</p>'), `got: ${html}`);
	});
});

// ── renderWarpBody ────────────────────────────────────────────────────────────

describe('renderWarpBody', () => {
	it('returns empty array for empty input', () => {
		assert.deepEqual(renderWarpBody(''), []);
		assert.deepEqual(renderWarpBody('   '), []);
	});

	it('splits body into sections by ## headings', () => {
		const md = `## Asked

What is the question?

## Answer

**State:** done.

## Done

(none)`;
		const sections = renderWarpBody(md);
		assert.equal(sections.length, 3, `sections: ${JSON.stringify(sections.map((s) => s.heading))}`);
		assert.equal(sections[0].heading, 'Asked');
		assert.equal(sections[1].heading, 'Answer');
		assert.equal(sections[2].heading, 'Done');
	});

	it('renders section HTML with inline formatting', () => {
		const md = `## Answer

**State:** done. **Next:** continue.`;
		const sections = renderWarpBody(md);
		assert.equal(sections.length, 1);
		assert.ok(sections[0].html.includes('<strong>State:</strong>'), `got: ${sections[0].html}`);
		assert.ok(sections[0].html.includes('<strong>Next:</strong>'), `got: ${sections[0].html}`);
	});

	it('nests ### sub-headings inside the current section', () => {
		const md = `## Answer

**State:** doing it.

### Sub-section

Detail here.`;
		const sections = renderWarpBody(md);
		// The ### sub-heading starts a new section entry.
		const subSection = sections.find((s) => s.heading === 'Sub-section');
		assert.ok(subSection, `sections: ${JSON.stringify(sections.map((s) => s.heading))}`);
		assert.equal(subSection.level, 3);
	});

	it('captures content before the first heading', () => {
		const md = `Preamble paragraph.

## Asked

The question.`;
		const sections = renderWarpBody(md);
		// Preamble gathers under an implicit empty heading.
		const preamble = sections.find((s) => s.heading === '');
		assert.ok(preamble, `sections: ${JSON.stringify(sections.map((s) => s.heading))}`);
		assert.ok(preamble.html.includes('Preamble'), `got: ${preamble.html}`);
	});

	it('returns correct heading levels', () => {
		const md = `## H2\n\ntext\n\n### H3\n\ntext`;
		const sections = renderWarpBody(md);
		assert.equal(sections[0].level, 2);
		assert.equal(sections[1].level, 3);
	});

	it('handles (none) in Done section gracefully', () => {
		const md = `## Done\n\n(none)`;
		const sections = renderWarpBody(md);
		assert.equal(sections.length, 1);
		assert.ok(sections[0].html.includes('(none)'), `got: ${sections[0].html}`);
	});
});
