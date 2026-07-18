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

export type MarkdownBlock =
	| { kind: 'heading'; level: number; text: string }
	| { kind: 'paragraph' | 'quote'; text: string }
	| { kind: 'list'; ordered: boolean; items: string[] }
	| { kind: 'code'; text: string };
export type InlineToken =
	| { kind: 'text' | 'strong'; text: string }
	| { kind: 'link'; text: string; href: string | null; target: string | null };

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

function internalTarget(currentPath: string, href: string, knownPaths: Set<string>): string | null {
	const clean = href.split('#', 1)[0].split('?', 1)[0];
	if (!clean || /^[a-z][a-z0-9+.-]*:/i.test(clean) || clean.startsWith('//')) return null;
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
	return knownPaths.has(target) ? target : null;
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
			tokens.push({
				kind: 'link',
				text: match[1],
				href: /^(https?:|mailto:)/i.test(rawHref) ? rawHref : null,
				target: internalTarget(currentPath, rawHref, knownPaths)
			});
		}
		cursor = match.index! + match[0].length;
	}
	if (cursor < text.length) tokens.push({ kind: 'text', text: text.slice(cursor) });
	return tokens;
}

export class SurfaceAuthError extends Error {}
export async function fetchSurface(fetchImpl: typeof fetch = fetch): Promise<SurfaceResponse> {
	const res = await fetchImpl('/v1/dashboard/surface', { headers: { accept: 'application/json' } });
	if (res.status === 401) throw new SurfaceAuthError('unauthenticated');
	if (!res.ok) throw new Error(`surface fetch failed: ${res.status}`);
	return (await res.json()) as SurfaceResponse;
}
