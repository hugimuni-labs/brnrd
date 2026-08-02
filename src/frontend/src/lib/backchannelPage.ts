import { headingAnchor } from './surface.ts';

// #875 v2: the resident-authored half of the backchannel lens. The page
// (`surface/backchannel.md`, shipped raw by `/v1/dashboard/surface`) is a
// sequence of `## ` sections in a minimal grammar — see design-backchannel.md
// §The v2 shape and the live page itself for the grammar this parses:
//
//   ## <headline>
//
//   kind: decide | review | read | act      (optional)
//   refs: <ref> · <ref> · ...               (optional)
//   prompt: <one-line dispatch mandate>      (optional)
//
//   <free markdown body>
//
// The warp (design-work-layers.md) extends the same grammar with two rows
// for layer pages (`surface/layers/*.md`) — one item space, one grammar:
//
//   state: ember | banked | cold            (optional; heat, not status)
//   needs: <the named missing thing>         (optional)
//
// Only these rows are recognized, and only as a contiguous block
// immediately after the heading — the grammar is deliberately open below
// that: any other line (an unrecognized row, a stray label) ends the
// recognized block and everything from there on is the item's plain
// markdown body, rendered as prose rather than parsed as schema.

export type BackchannelItemKind = 'decide' | 'review' | 'read' | 'act';

/** Item heat (design-work-layers.md §The concept): ember = clarified,
 * dispatchable now · banked = clarified, deliberately held (`needs:` names
 * by whom/what) · cold = undefined. An item with no `state:` row is
 * *unstated* — cold by the design's own semantics (cold IS "undefined"),
 * but callers get `null` so a renderer can tell authored-cold from
 * never-stated and show the second as such. */
export type WarpHeat = 'ember' | 'banked' | 'cold';

const KNOWN_KINDS: ReadonlySet<string> = new Set<BackchannelItemKind>([
	'decide',
	'review',
	'read',
	'act'
]);

const KNOWN_HEATS: ReadonlySet<string> = new Set<WarpHeat>(['ember', 'banked', 'cold']);

export interface BackchannelRef {
	label: string;
	/** Present only for an explicit `[label](url)` ref. A bare kb-page-name
	 *  ref (the common case for internal pages today) renders as plain text
	 *  rather than a guessed link — see `parseRefs`. */
	href: string | null;
}

export interface AuthoredBackchannelItem {
	/** Stable within one parse of one page: position + heading slug. Not
	 *  stable across edits that reorder or rename — callers key lists on it
	 *  for `#each`, not as a durable identity. */
	key: string;
	headline: string;
	kind: BackchannelItemKind | null;
	/** Warp heat (`state:` row). Null = the row was absent or unparseable —
	 *  never-stated, distinct from an authored `cold`. */
	state: WarpHeat | null;
	/** The named missing thing (`needs:` row): a decision, a user act, a
	 *  dependency, a deploy. Free text; the row's value verbatim. */
	needs: string | null;
	refs: BackchannelRef[];
	prompt: string | null;
	/** Run ids this item ignited (`taken:` row, daemon-written at ignition —
	 *  THE WELD, #972). Empty when the item has never crossed into the shed. */
	taken: string[];
	bodyMarkdown: string;
}

const ROW_RE = /^(kind|state|needs|refs|prompt|taken):[ \t]*(.*)$/;
const HEADING_RE = /^##[ \t]+(.*)$/;

/** A single markdown-link ref (`[label](url)`), anchored to the whole
 * segment — a link that merely appears inside a longer label is not this
 * shape and stays plain text, same as any other unrecognized construct here. */
const REF_LINK_RE = /^\[([^\]]+)]\(([^)\s]+)(?:\s+"[^"]*")?\)$/;

/** The forge's own qualified shorthand, `owner/repo#N` — the multi-repo
 * ground state's ref grammar (design-work-layers.md §Storage cosmology).
 * Deterministically resolvable, so it earns an href; `/issues/N` is safe for
 * PRs too (the forge redirects). A *bare* `#N` is deliberately not this
 * shape: on an account-global surface it names no repo, and an ambiguity
 * must render as one — plain text — never as a confidently guessed link. */
const FORGE_REF_RE = /^([\w.-]+\/[\w.-]+)#(\d+)$/;

/** Split a `refs:` value on its `·` separator into individual refs, each
 * either an explicit link, a qualified forge shorthand, or a bare label.
 * Never fabricates an `href` for a bare kb-page name or a bare `#N` — the
 * module has no reliable way to resolve either without guessing, and a
 * guessed link that 404s is worse than a label with none. */
export function parseRefs(raw: string): BackchannelRef[] {
	return raw
		.split('·')
		.map((segment) => segment.trim())
		.filter((segment) => segment.length > 0)
		.map((segment) => {
			const link = REF_LINK_RE.exec(segment);
			if (link) return { label: link[1], href: link[2] };
			const forge = FORGE_REF_RE.exec(segment);
			if (forge) {
				return { label: segment, href: `https://github.com/${forge[1]}/issues/${forge[2]}` };
			}
			return { label: segment, href: null };
		});
}

/** Parse `surface/backchannel.md` (or any page in its grammar) into the
 * resident-authored items it carries. Content before the first `## ` heading
 * (the page title, the grammar note) is preamble, not an item, and is
 * dropped. An empty or headingless page returns `[]`. Document order is
 * preserved — the grammar's priority signal is position, not a field. */
export function parseBackchannelPage(markdown: string): AuthoredBackchannelItem[] {
	const lines = (markdown ?? '').replace(/\r\n/g, '\n').split('\n');
	const items: AuthoredBackchannelItem[] = [];
	let i = 0;
	while (i < lines.length && !HEADING_RE.test(lines[i])) i += 1;

	let index = 0;
	while (i < lines.length) {
		const heading = HEADING_RE.exec(lines[i]);
		if (!heading) {
			i += 1;
			continue;
		}
		const headline = heading[1].trim();
		i += 1;
		// Heading and its row block are conventionally separated by one blank
		// line; tolerate its absence too rather than mis-parsing a page that
		// skips it.
		if (i < lines.length && lines[i].trim() === '') i += 1;

		let kind: BackchannelItemKind | null = null;
		let state: WarpHeat | null = null;
		let needs: string | null = null;
		let refs: BackchannelRef[] = [];
		let prompt: string | null = null;
		let taken: string[] = [];
		while (i < lines.length) {
			const row = ROW_RE.exec(lines[i]);
			if (!row) break;
			const [, key, value] = row;
			if (key === 'kind' && kind === null) {
				const candidate = value.trim().toLowerCase();
				if (KNOWN_KINDS.has(candidate)) kind = candidate as BackchannelItemKind;
			} else if (key === 'state' && state === null) {
				const candidate = value.trim().toLowerCase();
				if (KNOWN_HEATS.has(candidate)) state = candidate as WarpHeat;
			} else if (key === 'needs' && needs === null) {
				needs = value.trim() || null;
			} else if (key === 'refs' && refs.length === 0) {
				refs = parseRefs(value);
			} else if (key === 'prompt' && prompt === null) {
				prompt = value.trim() || null;
			} else if (key === 'taken' && taken.length === 0) {
				// THE WELD (#972): the run ids this item ignited, written by the
				// daemon at ignition (`weld.mark_taken`) — space-separated on one
				// row, ancestry the renderer shows as a back-pointer.
				taken = value.trim().split(/\s+/).filter(Boolean);
			}
			// A row whose key matches but whose value didn't parse (an unknown
			// kind, a repeated row) is still consumed here rather than leaked
			// into the body as a stray `key: value` prose line — the row shape
			// was clearly meant as schema, just not a usable instance of it.
			i += 1;
		}

		const bodyLines: string[] = [];
		while (i < lines.length && !HEADING_RE.test(lines[i])) {
			bodyLines.push(lines[i]);
			i += 1;
		}
		while (bodyLines.length && bodyLines[0].trim() === '') bodyLines.shift();
		while (bodyLines.length && bodyLines[bodyLines.length - 1].trim() === '') bodyLines.pop();

		items.push({
			key: `${index}:${headingAnchor(headline)}`,
			headline,
			kind,
			state,
			needs,
			refs,
			prompt,
			taken,
			bodyMarkdown: bodyLines.join('\n')
		});
		index += 1;
	}
	return items;
}
