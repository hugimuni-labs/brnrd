// A Wyrd run node, composed from the corpus mirror the dashboard already has.
//
// The durable node lives in the resident's home as three kinds of file, each
// with a different owner:
//
//   runs/<slug>/<run>/state.md              daemon-attested frame
//   runs/<slug>/<run>/body.md               resident-authored woven body
//   runs/<slug>/<run>/messages/NNNNNN-*.md  receipted outbound traffic
//
// All three already ride `GET /v1/dashboard/surface` as `layer: 'runs'` files,
// so this module is a pure composition over that response — no new endpoint,
// no second copy of the data.

import type { RunLedgerRow } from './runLedger';
import type { SurfaceFile, SurfaceResponse } from './surface';

export interface FrontmatterDocument {
	metadata: Record<string, string>;
	body: string;
}

export interface RunMessage extends FrontmatterDocument {
	file: SurfaceFile;
	/** Zero-padded write-order prefix from the filename; -1 when absent. */
	sequence: number;
}

export interface RunNode {
	repoSlug: string;
	runId: string;
	state: SurfaceFile | null;
	body: SurfaceFile | null;
	messages: RunMessage[];
	/** False when the corpus carries no file at all under this node's prefix. */
	mirrored: boolean;
}

// Mirror of `account._slug` (src/brr/account.py): every run of characters
// outside [A-Za-z0-9_.-] collapses to '-', leading/trailing separators are
// stripped, and an empty result becomes 'home'. The href must reproduce the
// *directory name the daemon actually wrote*, or the page can only ever
// report "not mirrored".
function slugSegment(value: string, fallback: string): string {
	const text = value
		.trim()
		.replace(/[^A-Za-z0-9_.-]+/g, '-')
		.replace(/^[-._]+|[-._]+$/g, '');
	return text || fallback;
}

/** Account run directories key on the same org__repo slug as the rest of home. */
export function repoRunSlug(repoLabel: string | null | undefined): string {
	return slugSegment((repoLabel ?? '').replaceAll('/', '__'), 'home');
}

/** `account.run_dir` sanitizes the run id into the directory name too. */
export function runIdSlug(runId: string | null | undefined): string {
	return slugSegment(runId ?? '', 'unknown-run');
}

/** Route to the run node page for one ledger/live run. */
export function runNodeHref(repoLabel: string | null | undefined, runId: string): string {
	return `/runs/${encodeURIComponent(repoRunSlug(repoLabel))}/${encodeURIComponent(runIdSlug(runId))}`;
}

/**
 * Route to the node a *corpus path* belongs to, or null when it is not a run
 * file. This is what turns a `runs/<slug>/<run>/…` link inside mirrored prose
 * into a real edge between nodes, using only paths already in the response.
 */
export function runNodeHrefForPath(path: string): string | null {
	const parts = path.split('/');
	if (parts.length < 3 || parts[0] !== 'runs') return null;
	const [, slug, run] = parts;
	if (!slug || !run) return null;
	return `/runs/${encodeURIComponent(slug)}/${encodeURIComponent(run)}`;
}

/** Select the receipt for this node without bleeding across account repos. */
export function runLedgerRowsForNode(
	rows: RunLedgerRow[],
	repoSlug: string,
	runId: string
): RunLedgerRow[] {
	const wantedRun = runIdSlug(runId);
	return rows.filter(
		(row) =>
			repoRunSlug(row.repo_label) === repoSlug && runIdSlug(row.run_id ?? '') === wantedRun
	);
}

/**
 * Split the deliberately-flat YAML header `state.md` and message records use.
 *
 * Deliberately not a YAML parser: both writers emit `key: value` lines with no
 * nesting, quoting, or multi-line scalars (see `message_store._render` and
 * `daemon._persist_run_state_doc`).
 */
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
	return {
		metadata,
		body: lines
			.slice(end + 1)
			.join('\n')
			.trim()
	};
}

function messageSequence(path: string): number {
	const name = path.split('/').at(-1) ?? '';
	const digits = /^(\d+)-/.exec(name);
	return digits ? Number.parseInt(digits[1], 10) : -1;
}

/**
 * Compose one node from the flat corpus response.
 *
 * Message order is the store's own write order (the `%06d` filename prefix
 * assigned in `message_store.stage`), not `created_at`: the sequence is always
 * present and always monotonic, while `created_at` is optional frontmatter and
 * ties on same-second staging.
 */
export function runNodeFromSurface(
	data: SurfaceResponse,
	repoSlug: string,
	runId: string
): RunNode {
	const prefix = `runs/${repoSlug}/${runId}/`;
	const files = (data.files ?? []).filter(
		(file) => (file.layer ?? '') === 'runs' && file.path.startsWith(prefix)
	);
	const state = files.find((file) => file.path === `${prefix}state.md`) ?? null;
	const body = files.find((file) => file.path === `${prefix}body.md`) ?? null;
	const messages = files
		.filter((file) => file.path.startsWith(`${prefix}messages/`))
		.map((file) => ({
			file,
			sequence: messageSequence(file.path),
			...frontmatterDocument(file.markdown)
		}))
		.sort(
			(a, b) =>
				a.sequence - b.sequence ||
				a.file.path.localeCompare(b.file.path, undefined, { numeric: true })
		);
	return { repoSlug, runId, state, body, messages, mirrored: files.length > 0 };
}

// ── Frame fields ──────────────────────────────────────────────────────────
//
// `state.md`'s frontmatter is the daemon's attestation. Render it as named
// fields rather than dumping the whole map: the list below is what the writer
// (`daemon._persist_run_state_doc`) commits to, in the order a reader wants
// it, and anything the writer later grows still surfaces via the catch-all.

export const FRAME_FIELDS: Array<{ key: string; label: string }> = [
	{ key: 'status', label: 'status' },
	{ key: 'stage', label: 'stage' },
	{ key: 'source', label: 'source' },
	{ key: 'runner_name', label: 'runner' },
	{ key: 'runner_shell', label: 'shell' },
	{ key: 'runner_core', label: 'core' },
	{ key: 'event_id', label: 'event' },
	{ key: 'conversation_key', label: 'thread' },
	{ key: 'target_branch', label: 'target branch' },
	{ key: 'branch_name', label: 'branch' },
	{ key: 'publish_branch', label: 'published' },
	{ key: 'publish_status', label: 'publish' },
	{ key: 'success_signal', label: 'signal' }
];

// Rendered elsewhere on the page (run id, repo label) or host-local noise a
// remote reader cannot act on (pid, the local reply-archive path).
const FRAME_SUPPRESSED = ['run_id', 'repo_label', 'pid', 'reply_archive'];

export interface FrameField {
	label: string;
	value: string;
}

/** Ordered, non-empty frame fields; unknown keys keep their raw name, last. */
export function frameFields(metadata: Record<string, string>): FrameField[] {
	const fields: FrameField[] = [];
	const seen = new Set<string>(FRAME_SUPPRESSED);
	for (const { key, label } of FRAME_FIELDS) {
		seen.add(key);
		const value = metadata[key];
		if (value) fields.push({ label, value });
	}
	for (const [key, value] of Object.entries(metadata)) {
		if (!seen.has(key) && value) fields.push({ label: key, value });
	}
	return fields;
}

// ── Message presentation ─────────────────────────────────────────────────

/** The only statuses `message_store` writes, plus a catch-all. */
export type MessageTone = 'delivered' | 'pending' | 'undeliverable' | 'unknown';

export function messageTone(status: string | null | undefined): MessageTone {
	switch (status) {
		case 'delivered':
		case 'pending':
		case 'undeliverable':
			return status;
		default:
			return 'unknown';
	}
}

/** Where a message was aimed: an event, a gate, or a thread. */
export function messageTarget(metadata: Record<string, string>): string {
	if (metadata.target_event) return `event ${metadata.target_event}`;
	if (metadata.target_gate) return `gate ${metadata.target_gate}`;
	if (metadata.target_thread) return metadata.target_thread;
	return '';
}

/** Delivery instant when known, else the staging instant. */
export function messageInstant(metadata: Record<string, string>): string {
	return metadata.delivered_at || metadata.created_at || '';
}
