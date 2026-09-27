/**
 * Minimal trusted-content Markdown renderer for warp item prose bodies.
 *
 * Scope: the subset of Markdown that appears in surface/warp/*.md bodies —
 * ATX headings (## / ### / ####), paragraphs, fenced lists (- / *), bold
 * (**...**), italic (*...*), inline code (``...``), and horizontal rules.
 * No external dependencies; the content is resident-authored and trusted,
 * but we still HTML-escape text runs to avoid rendering accidents.
 *
 * Intentionally NOT supported: tables, images, HTML pass-through, footnotes,
 * blockquotes, nested lists beyond one level (rendered flat), link auto-detect
 * (the item bodies do not contain bare URLs as of this writing).
 */

// ── Escaping ─────────────────────────────────────────────────────────────────

function esc(text: string): string {
	return text
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

// ── Inline rendering ──────────────────────────────────────────────────────────

/**
 * Render inline Markdown spans inside a line of body text.
 * Order matters: code spans are parsed first so `**not bold**` inside
 * backticks stays literal.
 */
function renderInline(text: string): string {
	// Collect code spans verbatim, replace with placeholder, then escape and
	// substitute back so inline code is never re-parsed.
	// Placeholder: a string that cannot appear in markdown or escaped HTML.
	const PH = '\u{FFFE}';
	const codes: string[] = [];
	const withCodes = text.replace(/`([^`]+)`/g, (_, inner) => {
		codes.push(`<code>${esc(inner)}</code>`);
		return `${PH}${codes.length - 1}${PH}`;
	});

	// Escape the remaining text and then apply span markup.
	let html = esc(withCodes);

	// Bold (**...**) and italic (*...*) — bold first so **x** doesn't get
	// consumed by an italic pass.
	html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
	html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

	// Restore code spans. The placeholder ￾ is not a valid XML character
	// so it cannot appear in well-formed HTML generated above.
	html = html.replace(/￾(\d+)￾/g, (_, i) => codes[parseInt(i, 10)]);
	return html;
}

// ── Block rendering ───────────────────────────────────────────────────────────

type Block =
	| { kind: 'heading'; level: number; text: string }
	| { kind: 'paragraph'; lines: string[] }
	| { kind: 'list'; items: string[] }
	| { kind: 'hr' };

function parseBlocks(markdown: string): Block[] {
	const raw = markdown.replace(/\r\n/g, '\n').split('\n');
	const blocks: Block[] = [];
	let i = 0;

	while (i < raw.length) {
		const line = raw[i];

		// Blank line — separator; skip.
		if (line.trim() === '') {
			i += 1;
			continue;
		}

		// ATX heading.
		const headingMatch = /^(#{1,6})\s+(.+)$/.exec(line);
		if (headingMatch) {
			blocks.push({ kind: 'heading', level: headingMatch[1].length, text: headingMatch[2].trim() });
			i += 1;
			continue;
		}

		// Horizontal rule.
		if (/^[-*_]{3,}\s*$/.test(line)) {
			blocks.push({ kind: 'hr' });
			i += 1;
			continue;
		}

		// Unordered list item.
		if (/^[-*+]\s/.test(line)) {
			const items: string[] = [];
			while (i < raw.length && /^[-*+]\s/.test(raw[i])) {
				items.push(raw[i].replace(/^[-*+]\s+/, ''));
				i += 1;
				// Consume a trailing blank line within the list block.
				if (
					i < raw.length &&
					raw[i].trim() === '' &&
					i + 1 < raw.length &&
					/^[-*+]\s/.test(raw[i + 1])
				) {
					i += 1;
				}
			}
			blocks.push({ kind: 'list', items });
			continue;
		}

		// Paragraph: consume until blank line or a block-level trigger.
		const lines: string[] = [];
		while (
			i < raw.length &&
			raw[i].trim() !== '' &&
			!/^#{1,6}\s/.test(raw[i]) &&
			!/^[-*+]\s/.test(raw[i]) &&
			!/^[-*_]{3,}\s*$/.test(raw[i])
		) {
			lines.push(raw[i]);
			i += 1;
		}
		if (lines.length > 0) blocks.push({ kind: 'paragraph', lines });
	}
	return blocks;
}

function blocksToHtml(blocks: Block[]): string {
	return blocks
		.map((block) => {
			switch (block.kind) {
				case 'heading': {
					const tag = `h${Math.min(block.level, 6)}`;
					return `<${tag}>${renderInline(block.text)}</${tag}>`;
				}
				case 'paragraph':
					return `<p>${block.lines.map(renderInline).join('<br>')}</p>`;
				case 'list':
					return `<ul>${block.items.map((item) => `<li>${renderInline(item)}</li>`).join('')}</ul>`;
				case 'hr':
					return '<hr>';
			}
		})
		.join('');
}

// ── Public API ────────────────────────────────────────────────────────────────

export interface MarkdownSection {
	heading: string;
	level: number;
	html: string;
}

/**
 * Split a warp item prose body by its top-level `##` headings and render
 * each section's content as HTML. Sections above the first `##` heading
 * (rare) are gathered under an implicit empty-string heading.
 *
 * Returns an array of `{ heading, level, html }` objects, in document order.
 */
export function renderWarpBody(markdown: string): MarkdownSection[] {
	if (!markdown.trim()) return [];

	// Split the document into named sections by ATX headings at level ≤ 3.
	const sections: MarkdownSection[] = [];
	let currentHeading = '';
	let currentLevel = 0;
	let currentLines: string[] = [];

	for (const line of markdown.replace(/\r\n/g, '\n').split('\n')) {
		const headingMatch = /^(#{1,3})\s+(.+)$/.exec(line);
		if (headingMatch) {
			// Flush whatever accumulated before this heading.
			const content = currentLines.join('\n').trim();
			if (content || currentHeading) {
				sections.push({
					heading: currentHeading,
					level: currentLevel,
					html: blocksToHtml(parseBlocks(content))
				});
			}
			currentHeading = headingMatch[2].trim();
			currentLevel = headingMatch[1].length;
			currentLines = [];
		} else {
			currentLines.push(line);
		}
	}
	// Flush the last section.
	const content = currentLines.join('\n').trim();
	if (content || currentHeading) {
		sections.push({
			heading: currentHeading,
			level: currentLevel,
			html: blocksToHtml(parseBlocks(content))
		});
	}
	return sections;
}

/**
 * Render a full Markdown string to an HTML string.
 * Convenience wrapper over the section parser for cases that don't need
 * section-level navigation (e.g. a single section's content).
 */
export function renderMarkdown(markdown: string): string {
	return blocksToHtml(parseBlocks(markdown));
}
