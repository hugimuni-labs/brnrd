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

// `ResolvedPathname` (below) is a type-only import — it costs nothing at
// runtime and stays resolvable under plain `node --test`, unlike a *value*
// import of `resolve()` from `$app/paths`, which only exists inside
// SvelteKit's Vite build and breaks this module's node-run unit tests
// (`runNode.test.ts` imports these functions directly, no bundler in the
// loop). The URLs below are already built by hand with `encodeURIComponent`
// and match the `/runs/[repo]/[run]` route exactly, so the cast is honest,
// not a bypass — `svelte/no-navigation-without-resolve` only cares that the
// *type* the caller receives is `ResolvedPathname`.
import type { ResolvedPathname } from '$app/types';
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
export function runNodeHref(repoLabel: string | null | undefined, runId: string): ResolvedPathname {
	return `/runs/${encodeURIComponent(repoRunSlug(repoLabel))}/${encodeURIComponent(runIdSlug(runId))}` as ResolvedPathname;
}

/**
 * Route to the node a *corpus path* belongs to, or null when it is not a run
 * file. This is what turns a `runs/<slug>/<run>/…` link inside mirrored prose
 * into a real edge between nodes, using only paths already in the response.
 */
export function runNodeHrefForPath(path: string): ResolvedPathname | null {
	const parts = path.split('/');
	if (parts.length < 3 || parts[0] !== 'runs') return null;
	const [, slug, run] = parts;
	if (!slug || !run) return null;
	return `/runs/${encodeURIComponent(slug)}/${encodeURIComponent(run)}` as ResolvedPathname;
}

/** Select the receipt for this node without bleeding across account repos. */
export function runLedgerRowsForNode(
	rows: RunLedgerRow[],
	repoSlug: string,
	runId: string
): RunLedgerRow[] {
	const wantedRun = runIdSlug(runId);
	return rows.filter(
		(row) => repoRunSlug(row.repo_label) === repoSlug && runIdSlug(row.run_id ?? '') === wantedRun
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

// Rendered elsewhere on the page (run id, repo label; the dispatch edges get
// their own navigable footer) or host-local noise a remote reader cannot act
// on (pid, the local reply-archive path).
const FRAME_SUPPRESSED = [
	'run_id',
	'repo_label',
	'pid',
	'reply_archive',
	'parent_run_id',
	'child_run_ids'
];

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

// ── Dispatch edges ───────────────────────────────────────────────────────
//
// Every run is dispatched by someone (wyrd §1). `source` names the *kind* of
// dispatcher — a user gate, the schedule, a parent run — and the daemon's
// `parent_run_id` / `child_run_ids` frontmatter names the identity when the
// dispatcher or dispatchee is itself a run.
//
// A neighbour is only a link when its node is actually in this corpus
// snapshot. An unmirrored neighbour is still a true edge and still named; it
// simply has nowhere to go yet, and saying so beats a href that 404s into
// "node not mirrored".

export interface DispatchEdge {
	runId: string;
	href: ResolvedPathname | null;
}

export interface DispatchEdges {
	/** Prose for a dispatcher that is not a run: the thread, or the schedule. */
	origin: string;
	parent: DispatchEdge | null;
	children: DispatchEdge[];
}

function edgeTo(repoSlug: string, runId: string, mirrored: Set<string>): DispatchEdge {
	const slug = runIdSlug(runId);
	const href = mirrored.has(`runs/${repoSlug}/${slug}/state.md`)
		? (`/runs/${encodeURIComponent(repoSlug)}/${encodeURIComponent(slug)}` as ResolvedPathname)
		: null;
	return { runId, href };
}

/**
 * Describe how this node hangs off the tree.
 *
 * Sibling edges are deliberately absent: two children of one parent are
 * related through it, and rendering that as a direct edge would invent
 * structure the daemon never recorded — the exact move that let a worker pass
 * a sibling's receipt off as its own (wyrd §3).
 */
export function dispatchEdges(
	metadata: Record<string, string>,
	repoSlug: string,
	mirroredPaths: Set<string>
): DispatchEdges {
	const parentId = (metadata.parent_run_id ?? '').trim();
	const children = (metadata.child_run_ids ?? '')
		.split(',')
		.map((item) => item.trim())
		.filter(Boolean)
		.map((runId) => edgeTo(repoSlug, runId, mirroredPaths));
	let origin = '';
	if (!parentId) {
		const source = (metadata.source ?? '').trim();
		if (source === 'schedule') origin = 'a scheduled wake';
		else if (source) origin = metadata.conversation_key || `the ${source} thread`;
	}
	return {
		origin,
		parent: parentId ? edgeTo(repoSlug, parentId, mirroredPaths) : null,
		children
	};
}

// ── Message presentation ─────────────────────────────────────────────────

/** The only statuses `message_store` writes, plus a catch-all. */
export type MessageTone = 'delivered' | 'collected' | 'pending' | 'undeliverable' | 'unknown';

export function messageTone(status: string | null | undefined): MessageTone {
	switch (status) {
		case 'delivered':
		case 'collected':
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

// ── The targeted view (loom-as-spine) ────────────────────────────────────
//
// The selected loom frame shows a *smaller and more targeted* read of the
// node than the standalone page (maintainer, 2026-07-19: "keep the loom as
// the spine"). Navigating away costs the reader their position in the band,
// so the band stays put and the frame fills; the `/runs/...` page remains the
// addressable deep link for sharing.

/**
 * The `## Now` section of a run body, or the whole body when it has none.
 *
 * Mirrors `daemon._card_now_projection`, deliberately and by hand: both sides
 * read the same resident-authored Markdown, and the compact projection is a
 * presentation rule, not data the writer should have to duplicate. One-section
 * legacy cards stay valid — an absent `## Now` means the whole body *is* the
 * now.
 */
export function nowProjection(body: string): string {
	const lines = body.replace(/\r\n/g, '\n').split('\n');
	const start = lines.findIndex((line) => line.trim().toLowerCase() === '## now');
	if (start === -1) return body.trim();
	const projected: string[] = [];
	for (const line of lines.slice(start + 1)) {
		if (line.startsWith('## ')) break;
		projected.push(line);
	}
	return projected.join('\n').trim();
}

/**
 * Does this body carry anything outside its `## Now` section?
 *
 * The question the expand affordance actually asks. A body with no sections
 * at all is entirely the now, and a body whose only section is `## Now` has
 * nothing further to give — in both cases the projection already showed the
 * reader everything.
 */
export function hasSectionsBeyondNow(body: string): boolean {
	const lines = body.replace(/\r\n/g, '\n').split('\n');
	const headings = lines.filter((line) => line.startsWith('## '));
	if (headings.length === 0) return false;
	if (headings.some((line) => line.trim().toLowerCase() !== '## now')) return true;
	// Only `## Now` headings: anything before the first one is body the
	// projection dropped.
	const first = lines.findIndex((line) => line.startsWith('## '));
	return lines.slice(0, first).join('\n').trim() !== '';
}

/**
 * Pull one `## Heading` section out of a Markdown body.
 *
 * The run node's produce arrives as ordinary Markdown in `state.md` rather
 * than as a parallel JSON schema — the daemon already renders relic icons and
 * links, and the alternative was a third hand-mirrored copy of the relic
 * vocabulary (`relics._ICONS` → `runLedger.RELIC_ICONS` was already two).
 * Headings and links are the interchange format; this is the only reader.
 */
export function bodySection(body: string, heading: string): string {
	const lines = body.replace(/\r\n/g, '\n').split('\n');
	const wanted = `## ${heading}`.toLowerCase();
	const start = lines.findIndex((line) => line.trim().toLowerCase() === wanted);
	if (start === -1) return '';
	const collected: string[] = [];
	for (const line of lines.slice(start + 1)) {
		if (line.startsWith('## ')) break;
		collected.push(line);
	}
	return collected.join('\n').trim();
}

export interface NodeDigest {
	/** Present only when the node is mirrored at all. */
	mirrored: boolean;
	status: string;
	stage: string;
	runner: string;
	/** The `## Now` projection of the body; '' when no body exists yet. */
	now: string;
	/**
	 * The run's own produce manifest, from the attested frame. '' when the
	 * run has made nothing yet — or when its node predates produce being
	 * written to `state.md` at all, which is not the same thing and is why
	 * the renderer must not print "produced nothing" over an empty string.
	 */
	produce: string;
	messageCount: number;
	/**
	 * The mood handle the run's own frame recorded (#566), '' when it set
	 * none. Name only: the frame is a text record, so nothing here resolves a
	 * glyph — and a closed run's chip renders the bare handle rather than
	 * looking one up, which is the honest answer anyway.
	 */
	mood: string;
	/** True when expanding would actually reveal something more. */
	hasMore: boolean;
}

/** Everything the selected frame needs, without composing the full page. */
/**
 * The run's identity as the LiveRuns card speaks it — one grammar for both
 * renderings (2026-07-21: "the first card's visual language is better, the
 * second more readable — best of both"). Composed by the page from whichever
 * source knows the run (live packet or ledger row) so the node panel can wear
 * the card's header: colored status word + age, name + spawn chip, repo ·
 * kind, runner line. Fields are null when the source doesn't know them; the
 * panel falls back to the node's own digest.
 */
export interface NodeIdentity {
	/** Colored status word — live phase while running, else level/status. */
	status: string;
	name: string | null;
	context: string | null;
	runner: string | null;
	spawn: boolean;
	age: string | null;
	/**
	 * The run's mood handle (#566) — from the live packet while it burns, from
	 * the frame's `mood:` field once it has closed. Null when the run set none.
	 */
	mood: string | null;
	/**
	 * The glyph the daemon resolved for that handle, and only ever that:
	 * null for an unknown handle, and null for every closed run (the frame
	 * records the handle, not the face). A null glyph means the chip renders
	 * the bare name — it never means "pick a default face".
	 */
	moodGlyph: string | null;
	/**
	 * The face's cycles and its resting frame, live-packet only. A closed run
	 * has neither: its frame records the handle the resident wrote and nothing
	 * the daemon resolved from it, and this frontend owns no emote table by
	 * design — so a closed run's chip is the bare name. That is honest, not
	 * good; fixing it means the daemon writing the resolved face into the run
	 * frame at closeout, which is a daemon-side change, not one this file can
	 * make by guessing.
	 */
	moodFrames: string[][] | null;
	moodRest: string | null;
	moodPitch: number | null;
}

export function nodeDigest(node: RunNode): NodeDigest {
	const frame = node.state ? frontmatterDocument(node.state.markdown) : null;
	const body = node.body ? node.body.markdown : '';
	const now = body ? nowProjection(body) : '';
	return {
		mirrored: node.mirrored,
		status: frame?.metadata.status ?? '',
		stage: frame?.metadata.stage ?? '',
		runner: frame?.metadata.runner_name ?? '',
		now,
		produce: frame ? bodySection(frame.body, 'Produce') : '',
		mood: frame?.metadata.mood ?? '',
		messageCount: node.messages.length,
		// Only offer the expand when it reveals something the reader cannot
		// already see. Comparing the projection against the raw body is not
		// that test — a body that is *only* a `## Now` section still differs
		// from its projection by the heading line, which would arm an expand
		// that shows the same words twice.
		hasMore: node.messages.length > 0 || hasSectionsBeyondNow(body)
	};
}
