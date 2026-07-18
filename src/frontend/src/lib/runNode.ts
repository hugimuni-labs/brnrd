import type { SurfaceFile, SurfaceResponse } from './surface';

export interface FrontmatterDocument {
	metadata: Record<string, string>;
	body: string;
}

export interface RunMessage extends FrontmatterDocument {
	file: SurfaceFile;
}

export interface RunNode {
	repoSlug: string;
	runId: string;
	state: SurfaceFile | null;
	body: SurfaceFile | null;
	messages: RunMessage[];
}

/** Account run directories use the same org__repo slug as the rest of home. */
export function repoRunSlug(repoLabel: string | null | undefined): string {
	const label = repoLabel?.trim();
	return label ? label.replaceAll('/', '__') : 'local';
}

export function runNodeHref(repoLabel: string | null | undefined, runId: string): string {
	return `/runs/${encodeURIComponent(repoRunSlug(repoLabel))}/${encodeURIComponent(runId)}`;
}

/** Split the deliberately-flat YAML header used by state and message records. */
export function frontmatterDocument(markdown: string): FrontmatterDocument {
	const lines = markdown.replace(/\r\n/g, '\n').split('\n');
	if (lines[0] !== '---') return { metadata: {}, body: markdown.trim() };
	const end = lines.indexOf('---', 1);
	if (end === -1) return { metadata: {}, body: markdown.trim() };
	const metadata: Record<string, string> = {};
	for (const line of lines.slice(1, end)) {
		const match = /^([^:#][^:]*):\s*(.*)$/.exec(line);
		if (match) metadata[match[1].trim()] = match[2].trim();
	}
	return { metadata, body: lines.slice(end + 1).join('\n').trim() };
}

/** Compose one Wyrd node from the flat, mirrored corpus response. */
export function runNodeFromSurface(
	data: SurfaceResponse,
	repoSlug: string,
	runId: string
): RunNode {
	const prefix = `runs/${repoSlug}/${runId}/`;
	const files = data.files.filter((file) => file.layer === 'runs' && file.path.startsWith(prefix));
	const state = files.find((file) => file.path === `${prefix}state.md`) ?? null;
	const body = files.find((file) => file.path === `${prefix}body.md`) ?? null;
	const messages = files
		.filter((file) => file.path.startsWith(`${prefix}messages/`))
		.sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }))
		.map((file) => ({ file, ...frontmatterDocument(file.markdown) }));
	return { repoSlug, runId, state, body, messages };
}
