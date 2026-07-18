export interface SurfaceFile {
	path: string;
	markdown: string;
	// Corpus join: which layer this home-relative page belongs to, and whether
	// its mirror was capped by the gate. Both optional so a pre-corpus mirror
	// (surface-only, no layer key) still renders — missing layer = 'authored'.
	layer?: string;
	truncated?: boolean;
}
export interface SurfaceResponse {
	generated_at: string;
	files: SurfaceFile[];
	reported_at: string | null;
}

// Reading order for the corpus browser: what the resident authored, then the
// knowledge it curated, then the run replies that knowledge archives.
export const LAYER_ORDER = ['authored', 'knowledge', 'replies'] as const;
export const LAYER_LABELS: Record<string, string> = {
	authored: 'surface',
	knowledge: 'knowledge',
	replies: 'replies'
};

export interface LayerGroup {
	layer: string;
	label: string;
	files: SurfaceFile[];
}

export function fileLayer(file: SurfaceFile): string {
	return file.layer ?? 'authored';
}

/** Group corpus files by layer in reading order; empty layers are dropped. */
export function groupByLayer(files: SurfaceFile[]): LayerGroup[] {
	const buckets = new Map<string, SurfaceFile[]>();
	for (const file of files) {
		const layer = fileLayer(file);
		const bucket = buckets.get(layer) ?? [];
		bucket.push(file);
		buckets.set(layer, bucket);
	}
	const ordered: LayerGroup[] = [];
	const emit = (layer: string) => {
		const bucket = buckets.get(layer);
		if (bucket && bucket.length) {
			ordered.push({ layer, label: LAYER_LABELS[layer] ?? layer, files: bucket });
			buckets.delete(layer);
		}
	};
	for (const layer of LAYER_ORDER) emit(layer);
	// Any unrecognized layer follows the known order rather than vanishing.
	for (const layer of [...buckets.keys()]) emit(layer);
	return ordered;
}

// Returns the filename without its directory prefix.
export function basename(path: string): string {
	const i = path.lastIndexOf('/');
	return i === -1 ? path : path.slice(i + 1);
}

// Collapsible sub-directory key within a layer's nav section. Derive this from
// the mirrored path instead of prescribing the corpus' free-form directories:
// knowledge/repos/Gurio__brr/page.md -> repos/Gurio__brr
// knowledge/replies/Gurio__brr/run.md -> Gurio__brr
// Authored pages intentionally remain a small flat menu.
function fileDir(path: string, layer: string): string | null {
	if (layer === 'authored') return null;
	const prefixes: Record<string, string[]> = {
		knowledge: ['knowledge'],
		replies: ['knowledge', 'replies']
	};
	const parts = path.split('/');
	const prefix = prefixes[layer] ?? [];
	const relative = prefix.every((part, i) => parts[i] === part)
		? parts.slice(prefix.length)
		: parts;
	const dirs = relative.slice(0, -1);
	return dirs.length > 0 ? dirs.join('/') : null;
}

// Exported alias used by WorkSurface to derive the parent dir on select,
// so the nav tree auto-expands to reveal the selected file.
export function fileDirKey(path: string, layer: string): string | null {
	return fileDir(path, layer);
}

export interface NavDir {
	key: string;
	count: number;
	files: SurfaceFile[];
}

export interface NavLayer {
	layer: string;
	label: string;
	count: number;
	// knowledge and replies layers group files into collapsible dirs;
	// authored renders as a flat list (dirs === null).
	dirs: NavDir[] | null;
	flatFiles: SurfaceFile[];
}

/** Build the collapsible nav tree from the flat corpus file list. */
export function buildNavTree(files: SurfaceFile[]): NavLayer[] {
	return groupByLayer(files).map(({ layer, label, files: layerFiles }) => {
		const useDirs = layer === 'knowledge' || layer === 'replies';
		if (!useDirs) {
			return { layer, label, count: layerFiles.length, dirs: null, flatFiles: layerFiles };
		}
		const dirMap = new Map<string, SurfaceFile[]>();
		for (const f of layerFiles) {
			const key = fileDir(f.path, layer) ?? '__ungrouped__';
			const bucket = dirMap.get(key) ?? [];
			bucket.push(f);
			dirMap.set(key, bucket);
		}
		const dirs: NavDir[] = [...dirMap.entries()].map(([key, dirFiles]) => ({
			key,
			count: dirFiles.length,
			files: dirFiles
		}));
		return { layer, label, count: layerFiles.length, dirs, flatFiles: [] };
	});
}

export type MarkdownBlock =
	| { kind: 'heading'; level: number; text: string }
	| { kind: 'paragraph' | 'quote'; text: string }
	| { kind: 'list'; ordered: boolean; items: string[] }
	| { kind: 'code'; text: string };
export type InlineToken =
	| { kind: 'text' | 'strong'; text: string }
	| {
			kind: 'link';
			text: string;
			href: string | null;
			target: string | null;
			// Fragment from the original href, for section auto-expansion on navigation.
			anchor: string | null;
	  };

export function markdownBlocks(markdown: string): MarkdownBlock[] {
	const lines = markdown.replace(/\r\n/g, '\n').split('\n');
	const blocks: MarkdownBlock[] = [];
	for (let i = 0; i < lines.length;) {
		const line = lines[i];
		if (!line.trim()) {
			i += 1;
			continue;
		}
		if (/^```/.test(line)) {
			const body: string[] = [];
			i += 1;
			while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
			if (i < lines.length) i += 1;
			blocks.push({ kind: 'code', text: body.join('\n') });
			continue;
		}
		const heading = /^(#{1,6})\s+(.*)$/.exec(line);
		if (heading) {
			blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() });
			i += 1;
			continue;
		}
		const item = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line);
		if (item) {
			const ordered = /\d+\./.test(item[2]);
			const items: string[] = [];
			while (i < lines.length) {
				const next = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(lines[i]);
				if (!next || /\d+\./.test(next[2]) !== ordered) break;
				items.push(next[3].trim());
				i += 1;
			}
			blocks.push({ kind: 'list', ordered, items });
			continue;
		}
		if (/^>\s?/.test(line)) {
			const body: string[] = [];
			while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ''));
			blocks.push({ kind: 'quote', text: body.join('\n') });
			continue;
		}
		const body = [line.trim()];
		i += 1;
		while (
			i < lines.length &&
			lines[i].trim() &&
			!/^(#{1,6})\s|^```|^>\s?|^(\s*)([-*]|\d+\.)\s+/.test(lines[i])
		) {
			body.push(lines[i].trim());
			i += 1;
		}
		blocks.push({ kind: 'paragraph', text: body.join(' ') });
	}
	return blocks;
}

function internalTarget(
	currentPath: string,
	href: string,
	knownPaths: Set<string>
): { target: string | null; anchor: string | null } {
	const hashIdx = href.indexOf('#');
	const fragment = hashIdx !== -1 ? href.slice(hashIdx + 1) || null : null;
	const clean = (hashIdx !== -1 ? href.slice(0, hashIdx) : href).split('?', 1)[0];
	if (!clean) {
		return {
			target: fragment && knownPaths.has(currentPath) ? currentPath : null,
			anchor: fragment
		};
	}
	if (/^[a-z][a-z0-9+.-]*:/i.test(clean) || clean.startsWith('//')) {
		return { target: null, anchor: fragment };
	}
	const parts = [
		...(clean.startsWith('/') ? [] : currentPath.split('/').slice(0, -1)),
		...clean.replace(/^\//, '').split('/')
	];
	const normalized: string[] = [];
	for (const part of parts) {
		if (!part || part === '.') continue;
		if (part === '..') normalized.pop();
		else normalized.push(part);
	}
	const target = normalized.join('/');
	return { target: knownPaths.has(target) ? target : null, anchor: fragment };
}

export function inlineTokens(
	text: string,
	currentPath: string,
	knownPaths: Set<string>
): InlineToken[] {
	const tokens: InlineToken[] = [];
	const pattern = /\[([^\]]+)]\(([^)\s]+)(?:\s+"[^"]*")?\)|\*\*([^*]+)\*\*/g;
	let cursor = 0;
	for (const match of text.matchAll(pattern)) {
		if (match.index! > cursor) tokens.push({ kind: 'text', text: text.slice(cursor, match.index) });
		if (match[3] !== undefined) tokens.push({ kind: 'strong', text: match[3] });
		else {
			const rawHref = match[2];
			const isExternal = /^(https?:|mailto:)/i.test(rawHref);
			const { target, anchor } = isExternal
				? { target: null, anchor: null }
				: internalTarget(currentPath, rawHref, knownPaths);
			tokens.push({
				kind: 'link',
				text: match[1],
				href: isExternal ? rawHref : null,
				target,
				anchor
			});
		}
		cursor = match.index! + match[0].length;
	}
	if (cursor < text.length) tokens.push({ kind: 'text', text: text.slice(cursor) });
	return tokens;
}

// ── Outline reader ────────────────────────────────────────────────────────────

// Pages with more than this many blocks switch from flat rendering to the
// collapsible outline view — keeps long knowledge pages navigable without
// burying the user under a wall of scroll.
export const SECTION_THRESHOLD = 14;

export interface PageSection {
	// The h2 (or h3) heading that opens this section; null for pre-heading preamble.
	heading: { kind: 'heading'; level: number; text: string } | null;
	// First content block after the heading — always shown when the section exists.
	preview: MarkdownBlock | null;
	// Remaining blocks, hidden until the section is expanded.
	tail: MarkdownBlock[];
}

/**
 * Split blocks into collapsible outline sections for the reading pane.
 * Returns null for short pages (≤ SECTION_THRESHOLD) or pages with no usable
 * split points — both render flat as before.
 * Splits on h2; falls back to h3 if the page has no h2 at all.
 */
export function splitIntoSections(blocks: MarkdownBlock[]): PageSection[] | null {
	if (blocks.length <= SECTION_THRESHOLD) return null;
	const hasH2 = blocks.some((b) => b.kind === 'heading' && b.level === 2);
	const splitLevel = hasH2 ? 2 : 3;
	const sections: PageSection[] = [];
	let current: MarkdownBlock[] = [];
	for (const block of blocks) {
		if (block.kind === 'heading' && block.level === splitLevel && current.length > 0) {
			sections.push(makeSection(current));
			current = [block];
		} else {
			current.push(block);
		}
	}
	if (current.length > 0) sections.push(makeSection(current));
	// If only one section, no real split points exist — fall back to flat render.
	return sections.length > 1 ? sections : null;
}

function makeSection(blocks: MarkdownBlock[]): PageSection {
	const first = blocks[0];
	const isHeading = first?.kind === 'heading';
	const heading = isHeading ? (first as { kind: 'heading'; level: number; text: string }) : null;
	const body = isHeading ? blocks.slice(1) : blocks;
	return { heading, preview: body[0] ?? null, tail: body.slice(1) };
}

// GitHub-style heading anchor slug: lowercase, strip non-word/space/hyphen, spaces→hyphens.
// Used to match a URL fragment against a section heading for auto-expansion on link navigation.
export function headingAnchor(text: string): string {
	return text
		.toLowerCase()
		.replace(/[^\w\s-]/g, '')
		.trim()
		.replace(/\s+/g, '-');
}

export class SurfaceAuthError extends Error {}
export async function fetchSurface(fetchImpl: typeof fetch = fetch): Promise<SurfaceResponse> {
	const res = await fetchImpl('/v1/dashboard/surface', { headers: { accept: 'application/json' } });
	if (res.status === 401) throw new SurfaceAuthError('unauthenticated');
	if (!res.ok) throw new Error(`surface fetch failed: ${res.status}`);
	return (await res.json()) as SurfaceResponse;
}
