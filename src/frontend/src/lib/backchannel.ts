import type { AuthoredBackchannelItem, BackchannelItemKind } from './backchannelPage';
import type { ConfigChangeRequestItem } from './configRequests';
import type { PRReviewItem } from './prReviewQueue';

export type BackchannelKind = 'pr' | 'config';

export interface BackchannelItem {
	key: string;
	kind: BackchannelKind;
	createdAt: string | null;
	headline: string;
	context: string;
	statusLabel: string;
	href: string;
	linkLabel: string;
}

function parseCreatedAt(value: string | null): number {
	if (!value) return Number.POSITIVE_INFINITY;
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

export function backchannelCount(
	prs: PRReviewItem[] | null | undefined,
	requests: ConfigChangeRequestItem[] | null | undefined
): number {
	return (prs?.length ?? 0) + (requests?.length ?? 0);
}

// Loading is a state, not an answer (#918's UI sibling, measured 2026-08-01:
// three loads of the same page rendered the §1 counter as 20 · "clear" · 4,
// because every intermediate feed arrival rendered as a finished verdict).
// The clear verdict and the final count may only render once every feed the
// sum spans has resolved — loaded or errored, but not still in flight.

/** The §1 "queue is clear" collapse is only true once all feeds resolved,
 * the sum is zero, and nothing is withheld. Before that, an empty count is
 * an unmeasured absence, not a zero. */
export function backchannelShowClear(
	feedsResolved: boolean,
	count: number,
	withheld: boolean
): boolean {
	return feedsResolved && count === 0 && !withheld;
}

/** Counter chip text: never presents an in-flight sum as a verdict, and
 * never a bare sum — the count spans two populations with different owners
 * (resident-authored surface items vs. rows derived from forge/config
 * feeds), so the chip always attributes: "N authored · M derived". */
export function backchannelChip(
	feedsResolved: boolean,
	authoredCount: number,
	derivedCount: number
): string {
	const attributed = `${authoredCount} authored · ${derivedCount} derived`;
	if (!feedsResolved) {
		return authoredCount + derivedCount === 0 ? 'counting…' : `${attributed} · counting…`;
	}
	if (authoredCount + derivedCount === 0) return 'nothing waiting';
	return attributed;
}

/** The briefing fold's whole state is one key (design-dashboard-briefing §3:
 * the full-prose render is the *open* state of exactly one row). Opening a
 * row closes whichever was open; tapping the open row closes it. */
export function toggleFold(open: string | null, key: string): string | null {
	return open === key ? null : key;
}

export interface NeedsPreviewRow {
	key: string;
	headline: string;
	kind: BackchannelItemKind | null;
}

/** The needs strip's collapsed preview: the top asks, decision/action first
 * (maintainer, 08-02: "the top item in all that should be a decision/action
 * ask"). Authored `decide`/`act` items lead, then the remaining authored
 * items — stable within each group, the file's own order preserved (order
 * *is* the priority within a band) — then the derived rows at the end, the
 * same tail position the full list gives them. Derived rows wear the kind
 * their action asks for: a PR is a `review`, a config request a `decide`. */
export function needsPreview(
	authored: AuthoredBackchannelItem[],
	derived: BackchannelItem[],
	limit: number
): NeedsPreviewRow[] {
	const asks = authored.filter((item) => item.kind === 'decide' || item.kind === 'act');
	const rest = authored.filter((item) => item.kind !== 'decide' && item.kind !== 'act');
	return [
		...[...asks, ...rest].map((item) => ({
			key: `a:${item.key}`,
			headline: item.headline,
			kind: item.kind
		})),
		...derived.map((item) => ({
			key: `d:${item.key}`,
			headline: item.headline,
			kind: (item.kind === 'pr' ? 'review' : 'decide') as BackchannelItemKind
		}))
	].slice(0, limit);
}

export function buildBackchannelItems(
	prs: PRReviewItem[],
	requests: ConfigChangeRequestItem[]
): BackchannelItem[] {
	const items: BackchannelItem[] = [
		...prs.map((pr) => ({
			key: `pr:${pr.repo_label}#${pr.number}`,
			kind: 'pr' as const,
			createdAt: pr.created_at,
			headline: `#${pr.number} ${pr.title || 'Untitled PR'}`,
			context: `${pr.repo_label || 'unknown repo'}${pr.author ? ` · ${pr.author}` : ''}`,
			statusLabel: pr.draft ? 'draft' : 'review',
			href: pr.url,
			linkLabel: 'open'
		})),
		...requests.map((request) => ({
			key: `config:${request.id}`,
			kind: 'config' as const,
			createdAt: request.created_at,
			headline: `${request.config_key}: ${request.current_value || '(unset)'} → ${request.requested_value}`,
			context: `${request.repo_label || 'unknown repo'}${request.reason ? ` · ${request.reason}` : ''}`,
			statusLabel: 'decide',
			href: request.approve_url,
			linkLabel: 'decide'
		}))
	];
	return items.sort((a, b) => {
		const byAge = parseCreatedAt(a.createdAt) - parseCreatedAt(b.createdAt);
		if (byAge !== 0) return byAge;
		return a.key.localeCompare(b.key);
	});
}
