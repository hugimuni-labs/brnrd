// Decisions space (#324 Phase 0): the resident's Current Planned State —
// per-repo active plan (CS5), cross-repo plan, decision ledger (CS7) —
// fetched from `GET /v1/dashboard/plans`, which reads the same
// `PUT /v1/daemons/plans` mirror the old Jinja `/plans` page rendered raw.
//
// Parsing lives here, client-side, on purpose: Phase 0 is read-only with
// no storage-schema decision. The plan file stays resident-authored prose;
// this module only *projects* structure out of the conventions the plan
// already uses (`Updated:` line, `## ` sections, numbered ranked moves),
// and degrades to plain sections when a convention is absent — a plan that
// parses badly still renders, it just renders flatter.

export interface RepoPlanIn {
	repo_label: string;
	plan_md: string;
	updated_at: string | null;
}

export interface PlansResponse {
	generated_at: string;
	plans: RepoPlanIn[];
	cross_repo_plan_md: string;
	decisions_md: string;
	reported_at: string | null;
}

export interface PlanSection {
	title: string;
	body: string;
	/** Ranked-move items when this section is the ranked list, else []. */
	moves: RankedMove[];
}

export interface RankedMove {
	rank: string;
	/** First line of the item — the move's headline. */
	label: string;
	/** Remaining lines of the item, if any. */
	detail: string;
}

export interface ParsedPlan {
	/** Date (YYYY-MM-DD) pulled from the plan's own `Updated:` line, if present. */
	updatedDate: string | null;
	sections: PlanSection[];
}

export interface DecisionEntry {
	title: string;
	/** Date (YYYY-MM-DD) pulled from the entry heading, if present. */
	date: string | null;
	body: string;
}

const DATE_RE = /(\d{4}-\d{2}-\d{2})/;

/** Split a markdown document on `## ` headings; text before the first
 * heading is returned under the given preamble title (empty title = drop
 * empty preambles silently). */
function splitSections(md: string): { title: string; body: string }[] {
	const out: { title: string; body: string }[] = [];
	let title = '';
	let buf: string[] = [];
	for (const line of md.split('\n')) {
		const m = /^##\s+(.*)/.exec(line);
		if (m) {
			if (title || buf.join('').trim()) out.push({ title, body: buf.join('\n').trim() });
			title = m[1].trim();
			buf = [];
		} else if (!/^#\s/.test(line)) {
			buf.push(line);
		}
	}
	if (title || buf.join('').trim()) out.push({ title, body: buf.join('\n').trim() });
	return out;
}

/** Parse a section body as a numbered ranked-move list. Returns [] when
 * the body doesn't look like one (fewer than 2 numbered items). */
function parseMoves(body: string): RankedMove[] {
	const moves: RankedMove[] = [];
	let current: { rank: string; lines: string[] } | null = null;
	for (const line of body.split('\n')) {
		const m = /^(\d+)\.\s+(.*)/.exec(line);
		if (m) {
			if (current) moves.push(finishMove(current));
			current = { rank: m[1], lines: [m[2]] };
		} else if (current && line.trim()) {
			current.lines.push(line.trim());
		}
	}
	if (current) moves.push(finishMove(current));
	return moves.length >= 2 ? moves : [];
}

function finishMove(m: { rank: string; lines: string[] }): RankedMove {
	const text = m.lines.join(' ');
	// Headline: the bold lead when present, else the first sentence-ish chunk.
	const bold = /\*\*(.+?)\*\*/.exec(text);
	const label = (bold ? bold[1] : (m.lines[0] ?? '')).replace(/\*\*/g, '').trim();
	const detail = text
		.replace(bold ? bold[0] : '', '')
		.replace(/\*\*/g, '')
		.replace(/^\s*[—–-]\s*/, '')
		.trim();
	return { rank: m.rank, label, detail };
}

export function parsePlan(md: string): ParsedPlan {
	const updated = /^Updated:\s*(.*)$/m.exec(md);
	const updatedDate = updated ? (DATE_RE.exec(updated[1])?.[1] ?? null) : null;
	const sections = splitSections(md).map((s) => ({
		...s,
		moves: /ranked/i.test(s.title) ? parseMoves(s.body) : []
	}));
	return { updatedDate, sections };
}

export function parseDecisions(md: string): DecisionEntry[] {
	return splitSections(md)
		.filter((s) => s.title)
		.map((s) => ({
			title: s.title.replace(/\s*\(\s*\d{4}-\d{2}-\d{2}[^)]*\)\s*$/, '').trim(),
			date: DATE_RE.exec(s.title)?.[1] ?? null,
			body: s.body
		}));
}

/** Days since a YYYY-MM-DD date (UTC-naive — day granularity is all the
 * staleness badge needs). Null in = null out. */
export function daysSince(date: string | null, now: number): number | null {
	if (!date) return null;
	const t = Date.parse(`${date}T00:00:00Z`);
	if (Number.isNaN(t)) return null;
	return Math.floor((now - t) / 86_400_000);
}

export class PlansAuthError extends Error {}

export async function fetchPlans(fetchImpl: typeof fetch = fetch): Promise<PlansResponse> {
	const res = await fetchImpl('/v1/dashboard/plans', {
		headers: { accept: 'application/json' }
	});
	if (res.status === 401) throw new PlansAuthError('unauthenticated');
	if (!res.ok) throw new Error(`plans fetch failed: ${res.status}`);
	return (await res.json()) as PlansResponse;
}
