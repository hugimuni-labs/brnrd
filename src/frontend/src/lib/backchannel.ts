import type { ConfigChangeRequestItem } from './configRequests';
import type { PRReviewItem } from './prReviewQueue';

// The needs-you strip's derived half: rows the daemon derives from forge
// and config feeds, not items anyone authored — authored asks now live in
// the warp itself (design-work-layers.md). This module used to also carry
// a resident-authored population (`backchannel*` naming, #875 v2); that
// half retired into the warp 2026-08-11 and is gone from here — see
// `BackchannelQueue.svelte` and `+page.svelte`'s needs-you strip, the two
// remaining consumers, for what's left.

export type DerivedAskKind = 'pr' | 'config';

export interface DerivedAskItem {
	key: string;
	kind: DerivedAskKind;
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

// Loading is a state, not an answer (#918's UI sibling, measured 2026-08-01:
// three loads of the same page rendered the §1 counter as 20 · "clear" · 4,
// because every intermediate feed arrival rendered as a finished verdict).
// The clear verdict and the final count may only render once every feed the
// sum spans has resolved — loaded or errored, but not still in flight.

/** The needs-you strip's "queue is clear" collapse is only true once every
 * feed resolved, the sum is zero, and nothing is withheld. Before that, an
 * empty count is an unmeasured absence, not a zero. */
export function derivedAsksShowClear(
	feedsResolved: boolean,
	count: number,
	withheld: boolean
): boolean {
	return feedsResolved && count === 0 && !withheld;
}

/** Counter chip text: never presents an in-flight sum as a verdict, and
 * never counts a draft PR — `buildDerivedAsks` is the one place that
 * filters drafts, so this chip, the strip's visibility, and its rows all
 * agree on the same population. */
export function derivedAsksChip(feedsResolved: boolean, count: number): string {
	if (!feedsResolved) return count === 0 ? 'counting…' : `${count} derived · counting…`;
	if (count === 0) return 'nothing waiting';
	return `${count} derived`;
}

/** Open PRs withheld from the needs-you count because they're still
 * drafts — a draft means "the resident isn't done with it" (workflow.md),
 * not "needs you". Informational only: render it as a quiet footnote if at
 * all, never as a row. */
export function draftPrCount(prs: PRReviewItem[] | null | undefined): number {
	return (prs ?? []).filter((pr) => pr.draft).length;
}

/** The rows a returning reader is asked to look at: open (non-draft) PRs
 * awaiting review, plus config-change requests awaiting a decision. A draft
 * PR is filtered here, at the one place every consumer (count, chip, rows)
 * reads from — never in a renderer, or some consumer would disagree with
 * another about what's waiting. */
export function buildDerivedAsks(
	prs: PRReviewItem[],
	requests: ConfigChangeRequestItem[]
): DerivedAskItem[] {
	const items: DerivedAskItem[] = [
		...prs
			.filter((pr) => !pr.draft)
			.map((pr) => ({
				key: `pr:${pr.repo_label}#${pr.number}`,
				kind: 'pr' as const,
				createdAt: pr.created_at,
				headline: `#${pr.number} ${pr.title || 'Untitled PR'}`,
				context: `${pr.repo_label || 'unknown repo'}${pr.author ? ` · ${pr.author}` : ''}`,
				statusLabel: 'review',
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
