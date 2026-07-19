// Loom slice 3 (#271): closed-run receipt feed. Types mirror the JSON
// `GET /v1/dashboard/run-ledger` returns, sourced from the daemon's local
// `.brr/run-ledger.jsonl` via `PUT /v1/daemons/run-ledger`.

// Run relics (#200/#317, kb/design-run-relics.md): one entry per thing the
// run produced. `kind` is the only required field — everything else is
// kind-specific (a `commit` carries `sha`/`subject`, an `issue` carries
// `number`/`action`, etc). Mirrors `brr.relics`'s JSON shape byte for byte;
// this type is deliberately loose (`Record<string, unknown>` underneath)
// because the backend's relic vocabulary is meant to grow without a
// frontend type-and-ship round trip for every new kind.
export interface RelicRecord {
	kind: string;
	url?: string | null;
	[key: string]: unknown;
}

// Mirrors `brr.relics._ICONS`. Kept in sync by hand today — noted in
// `kb/design-run-relics.md` as a follow-up to generate this from one
// source instead of two hand-maintained maps.
export const RELIC_ICONS: Record<string, string> = {
	summary: '📝',
	commit: '🔨',
	branch: '🌿',
	pr: '🔀',
	issue: '🎫',
	comment: '💬',
	kb: '📚',
	file: '📄',
	message: '✉️',
	reply: '🗣️'
};

export function relicIcon(kind: string): string {
	return RELIC_ICONS[kind] ?? '•';
}

/** Collapsed-receipt counts, e.g. `{ commit: 3, pr: 1 }` — excludes the
 * free-form `summary` relic, which is prose, not produce. Counts are
 * taken over *families* (see `groupRelicFamilies`), so a PR's absorbed
 * branch/commits don't triple-count the same piece of produce (#329). */
export function relicCounts(relicList: RelicRecord[]): Record<string, number> {
	const out: Record<string, number> = {};
	for (const fam of groupRelicFamilies(relicList)) {
		const r = fam.head;
		if (!r.kind || r.kind === 'summary') continue;
		out[r.kind] = (out[r.kind] ?? 0) + 1;
	}
	return out;
}

// ── Relic families (#329) ───────────────────────────────────────────
// Live maintainer feedback on the first real receipts: "branch, commit
// and PR are basically the same thing for a user." A family is one piece
// of produce: a `pr` head absorbs the `branch` relic and all `commit`
// relics; a `branch` head (no PR present) absorbs its commits; everything
// else is a family of one. The raw manifest stays complete — this is a
// render-side projection only.

export interface RelicFamily {
	head: AttributedRelic;
	/** Absorbed members (commits, branch) shown indented under the head. */
	members: AttributedRelic[];
}

/** True when a relic has nothing renderable — e.g. a commit relic with
 * neither sha nor subject (seen live: a bare 🔨 with only a `↳ via`
 * suffix). Filtered out rather than rendered as an empty line. */
export function isBlankRelic(r: RelicRecord): boolean {
	if (r.kind === 'commit') return !String(r.sha ?? '').trim() && !String(r.subject ?? '').trim();
	if (r.kind === 'branch') return !String(r.name ?? '').trim();
	return false;
}

export function groupRelicFamilies(relicList: AttributedRelic[]): RelicFamily[] {
	const list = relicList.filter((r) => r.kind !== 'summary' && !isBlankRelic(r));
	const prs = list.filter((r) => r.kind === 'pr');
	const branches = list.filter((r) => r.kind === 'branch');
	const commits = list.filter((r) => r.kind === 'commit');
	const rest = list.filter((r) => !['pr', 'branch', 'commit'].includes(r.kind));

	const families: RelicFamily[] = [];
	if (prs.length === 1) {
		// The clean, common case: one PR absorbs the whole code family.
		families.push({ head: prs[0], members: [...branches, ...commits] });
	} else if (prs.length === 0 && branches.length === 1) {
		families.push({ head: branches[0], members: commits });
	} else {
		// Ambiguous (multiple PRs — the manifest doesn't attribute commits
		// to PRs) or nothing to absorb: keep the flat shape rather than
		// guess an attribution wrong.
		for (const r of [...prs, ...branches, ...commits]) families.push({ head: r, members: [] });
	}
	for (const r of rest) families.push({ head: r, members: [] });
	return families;
}

/** Suffix for a collapsed family line: `· 3 commits`. */
export function familySuffix(fam: RelicFamily): string {
	const commits = fam.members.filter((m) => m.kind === 'commit').length;
	return commits > 0 ? ` · ${commits} commit${commits === 1 ? '' : 's'}` : '';
}

/** One-line label for a single relic, used in the expanded list. Falls
 * back to the kind name when no more specific field is present, so an
 * unrecognised future kind still renders something instead of "undefined". */
export function relicLabel(r: RelicRecord): string {
	switch (r.kind) {
		case 'commit':
			return `${String(r.sha ?? '').slice(0, 7)} ${r.subject ?? ''}`.trim();
		case 'branch':
			return String(r.name ?? 'branch');
		case 'pr':
			return `PR #${r.number ?? '?'}`;
		case 'issue':
			return `issue #${r.number ?? '?'}${r.action ? ` (${r.action})` : ''}`;
		case 'kb':
			return String(r.path ?? 'kb page');
		case 'file':
			return String(r.path ?? 'file');
		case 'comment':
			return String(r.on ?? 'comment');
		case 'message':
			return String(r.note ?? r.channel ?? 'message');
		case 'reply':
			return String(r.excerpt ?? 'reply');
		case 'summary':
			return String(r.text ?? '');
		default: {
			for (const field of ['text', 'path', 'note', 'name', 'on']) {
				const value = String(r[field] ?? '').trim();
				if (value) return value;
			}
			return r.kind;
		}
	}
}

export interface RunLedgerRow {
	run_id: string | null;
	event_id: string | null;
	started_at: string | null;
	ended_at: string | null;
	wall_clock_seconds: number | null;
	runner_shell: string | null;
	runner_core: string | null;
	// Core attestation: `runner_core` is what the Shell actually ran
	// (observed from its own result JSON at close); `core_expected` is what
	// the config pinned at dispatch; `core_mismatch` is the alarm bit —
	// true means the pin was not respected, null means unverifiable.
	core_expected: string | null;
	core_mismatch: boolean | null;
	// *Why* the pin was not respected, read from the Shell's own session
	// transcript — the result envelope declares success and carries no reason.
	// Null on clean runs and whenever the Shell records no refusal.
	substitution_reason: string | null;
	repo_label: string | null;
	source_system: string | null;
	name: string | null;
	external_refs: RelicRecord[] | null;
	parent_run_id: string | null;
	is_subspawn: boolean | null;
	tokens_input: number | null;
	tokens_output: number | null;
	tokens_cache_read: number | null;
	tokens_cache_creation: number | null;
	context_window_used: number | null;
	weekly_pct_delta: number | null;
	five_hour_pct_delta: number | null;
	usd_subscription_attributed: number | null;
	usd_credits_equivalent: number | null;
	estimate_vs_actual: string | null;
}

// A relic folded in from a child (sub-spawn) run, tagged with which run
// it actually came from so the expanded view can show "↳ via <run>"
// rather than silently attributing a child's produce to the parent.
export interface AttributedRelic extends RelicRecord {
	_from_run_id?: string;
}

export interface GroupedReceipt {
	row: RunLedgerRow;
	relics: AttributedRelic[];
	childRunIds: string[];
}

/** Fold sub-spawn rows into their parent for receipt rendering (the
 * maintainer's "list of subrun relics too" ask). Same join key
 * (`parent_run_id`/`is_subspawn`) `LiveRuns.svelte` already uses for the
 * live view — this is the closed-receipt side of that same relationship.
 *
 * A child whose parent isn't present in this page/limit window (parent
 * already scrolled past, or never in this batch) renders standalone
 * rather than silently vanishing — its relics still deserve a receipt.
 */
export function groupWithChildren(rows: RunLedgerRow[]): GroupedReceipt[] {
	const byId = new Map<string, RunLedgerRow>();
	for (const row of rows) {
		if (row.run_id) byId.set(row.run_id, row);
	}
	const childrenByParent = new Map<string, RunLedgerRow[]>();
	const parentless: RunLedgerRow[] = [];
	for (const row of rows) {
		if (row.is_subspawn && row.parent_run_id && byId.has(row.parent_run_id)) {
			const list = childrenByParent.get(row.parent_run_id) ?? [];
			list.push(row);
			childrenByParent.set(row.parent_run_id, list);
		} else {
			parentless.push(row);
		}
	}
	return parentless.map((row) => {
		const children = row.run_id ? (childrenByParent.get(row.run_id) ?? []) : [];
		const ownRelics: AttributedRelic[] = row.external_refs ?? [];
		const childRelics: AttributedRelic[] = children.flatMap((child) =>
			(child.external_refs ?? []).map((r) => ({ ...r, _from_run_id: child.run_id ?? undefined }))
		);
		return {
			row,
			relics: [...ownRelics, ...childRelics],
			childRunIds: children.map((c) => c.run_id).filter((id): id is string => Boolean(id))
		};
	});
}

export interface RunLedgerResponse {
	generated_at: string;
	rows: RunLedgerRow[];
	stale: boolean;
	reported_at: string | null;
}

export class RunLedgerAuthError extends Error {}

/** Fetches the closed-run receipt feed. Throws `RunLedgerAuthError` on a
 * 401 (no session cookie), same shape as the other dashboard fetchers. */
export async function fetchRunLedger(
	fetchImpl: typeof fetch = fetch,
	limit = 10,
	spanMs?: number
): Promise<RunLedgerResponse> {
	const params = new URLSearchParams({ limit: String(limit) });
	if (spanMs !== undefined) {
		params.set('span_seconds', String(Math.max(1, Math.round(spanMs / 1000))));
	}
	const res = await fetchImpl(`/v1/dashboard/run-ledger?${params.toString()}`, {
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new RunLedgerAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`run-ledger fetch failed: ${res.status}`);
	}
	return (await res.json()) as RunLedgerResponse;
}

export function durationLabel(seconds: number | null): string {
	if (seconds === null || seconds === undefined) return '—';
	if (seconds < 90) return `${Math.round(seconds)}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 90)
		return `${minutes}m ${Math.round(seconds % 60)
			.toString()
			.padStart(2, '0')}s`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ${minutes % 60}m`;
}

export function tokenLabel(value: number | null): string {
	if (value === null || value === undefined) return '—';
	return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(
		value
	);
}

export function signedPercentLabel(value: number | null): string {
	if (value === null || value === undefined) return '—';
	const sign = value > 0 ? '+' : '';
	return `${sign}${value.toFixed(2)}%`;
}

export function usdLabel(value: number | null): string {
	if (value === null || value === undefined) return '—';
	return new Intl.NumberFormat('en', {
		style: 'currency',
		currency: 'USD',
		maximumFractionDigits: 4
	}).format(value);
}

export function endedLabel(endedAt: string | null): string {
	if (!endedAt) return '—';
	const ended = Date.parse(endedAt);
	if (Number.isNaN(ended)) return '—';
	return new Date(ended).toLocaleTimeString();
}
