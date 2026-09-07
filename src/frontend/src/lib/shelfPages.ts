/**
 * The shelf (`surface/shelf/*.md`, home `surface/shelf/index.md` §The
 * contract): commissioned artifacts that expire by declaration rather than
 * by sweep. The pages already ride the authored corpus (`work_surface_files`
 * / `corpus_files` walk `surface/` recursively, `shelf/` included) and
 * publish to `/v1/dashboard/surface` with everything else — this module is
 * purely the read side: pick the shelf pages out of the corpus feed and
 * parse the two declared rows (`made:`, `keeps:`) a reader needs without
 * opening the page.
 *
 * The contract page describes one frontmatter shape (`made-for:` / `asked:`
 * / `keeps:` / `topics:`, a block before the `# ` title); the pages actually
 * written since (`stars-plan-…`, `show-hn-draft-…`, `karma-recon-…`) use a
 * looser one — title first, then bare `made:` / `keeps:` lines, no
 * `made-for:`/`asked:`/`topics:`. Both shapes are read here rather than
 * picking one and rendering the other's pages blank: `keeps:` and `made:`
 * (or `made-for:`+`asked:`) are found by scanning every line, independent of
 * order or position relative to the title.
 *
 * Value imports carry `.ts` extensions — `shelfPages.test.ts` runs under
 * node's own runner with no bundler in the loop (`futureShelf.ts` documents
 * the same rule).
 */

import { basename, fileLayer, type SurfaceFile } from './surface.ts';

export interface ShelfEntry {
	path: string;
	/** First `# ` line, stray `~~strikethrough~~` markers stripped. Falls
	 * back to the basename for a page that somehow has no heading at all. */
	title: string;
	/** The page's own `made:` row, or a `made-for: X · asked: evt-…` stand-in
	 * assembled from the older frontmatter shape. `null` when neither is
	 * declared — a fact worth showing as "undeclared", not hiding the row. */
	madeLine: string | null;
	/** The page's own `keeps:` row verbatim, or `null` when absent — per the
	 * shelf's own contract that page is not really a shelf page, but this
	 * reader still surfaces it rather than silently dropping content. */
	keepsLine: string | null;
	/** True when the page itself marked its `keeps:` condition met (a
	 * `keeps:` line naming "expired", a struck title) or its `keeps:` names a
	 * calendar date that has passed. Never computed from a free-text
	 * condition this reader cannot safely evaluate — the shelf's rule is
	 * "renders in place, nothing sweeps", so an unparsed condition stays
	 * open rather than guessed shut. */
	expired: boolean;
}

const SHELF_PREFIX = 'surface/shelf/';
const DATE_RE = /\b(\d{4})-(\d{2})-(\d{2})\b/;

function lineMatch(lines: string[], re: RegExp): string | null {
	for (const line of lines) {
		const m = re.exec(line.trim());
		if (m) return m[1].trim();
	}
	return null;
}

function pastDate(text: string, now: number): boolean {
	const m = DATE_RE.exec(text);
	if (!m) return false;
	const t = Date.parse(`${m[1]}-${m[2]}-${m[3]}T00:00:00Z`);
	return Number.isFinite(t) && t < now;
}

function extractDate(text: string | null): number | null {
	if (!text) return null;
	const m = DATE_RE.exec(text);
	if (!m) return null;
	const t = Date.parse(`${m[1]}-${m[2]}-${m[3]}T00:00:00Z`);
	return Number.isFinite(t) ? t : null;
}

function parseShelfFile(file: SurfaceFile, now: number): ShelfEntry {
	const lines = file.markdown.replace(/\r\n/g, '\n').split('\n');

	const rawTitle = lineMatch(lines, /^#\s+(.*)$/);
	const struck = rawTitle !== null && /^~~.*~~$/.test(rawTitle.trim());
	const title = (rawTitle ?? basename(file.path)).replace(/^~~\s*|\s*~~$/g, '').trim();

	const made = lineMatch(lines, /^made:\s*(.*)$/i);
	const madeFor = lineMatch(lines, /^made-for:\s*(.*)$/i);
	const asked = lineMatch(lines, /^asked:\s*(.*)$/i);
	const madeLine = made ?? (madeFor ? (asked ? `${madeFor} · ${asked}` : madeFor) : null);

	const keepsLine = lineMatch(lines, /^keeps:\s*(.*)$/i);
	const expired =
		struck || (keepsLine !== null && (/expired/i.test(keepsLine) || pastDate(keepsLine, now)));

	return { path: file.path, title, madeLine, keepsLine, expired };
}

/**
 * Pick the shelf pages out of the discovered corpus and parse them for
 * display. `index.md` (the contract page, not a commissioned artifact) is
 * excluded. Open pages sort before expired ones; within a bucket, the most
 * recent parseable date (from `made:`/`keeps:`, whichever names one) leads —
 * a page with no parseable date at all sorts after its dated siblings
 * without being dropped.
 */
export function shelfEntries(files: SurfaceFile[], now: number = Date.now()): ShelfEntry[] {
	const entries = files
		.filter(
			(f) =>
				fileLayer(f) === 'authored' &&
				f.path.startsWith(SHELF_PREFIX) &&
				basename(f.path) !== 'index.md'
		)
		.map((f) => parseShelfFile(f, now));

	return entries.sort((a, b) => {
		if (a.expired !== b.expired) return a.expired ? 1 : -1;
		const da = extractDate(a.madeLine) ?? extractDate(a.keepsLine);
		const db = extractDate(b.madeLine) ?? extractDate(b.keepsLine);
		if (da !== null && db !== null) return db - da;
		if (da !== null) return -1;
		if (db !== null) return 1;
		return a.path.localeCompare(b.path);
	});
}
