import type { WithheldLane } from './withheld';

// Slice 4 (#259): account-scoped PR-review queue. Types mirror the JSON
// `GET /v1/dashboard/pr-review-queue` returns, sourced from `gh pr list`
// via the daemon's `PUT /v1/daemons/pr-review-queue` publish.

export interface PRReviewItem {
	number: number;
	title: string;
	url: string;
	repo_label: string;
	created_at: string | null;
	draft: boolean;
	author: string;
}

export interface PRReviewQueueResponse {
	generated_at: string;
	prs: PRReviewItem[];
	stale: boolean;
	reported_at: string | null;
	withheld?: WithheldLane;
}

export class PRReviewQueueAuthError extends Error {}

/** Fetches the account-scoped open-PR review queue. Throws
 * `PRReviewQueueAuthError` on a 401 (no session cookie), same shape as the
 * quota and live-runs fetchers. */
export async function fetchPRReviewQueue(
	fetchImpl: typeof fetch = fetch
): Promise<PRReviewQueueResponse> {
	const res = await fetchImpl('/v1/dashboard/pr-review-queue', { credentials: 'include' });
	if (res.status === 401) {
		throw new PRReviewQueueAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`pr-review-queue fetch failed: ${res.status}`);
	}
	return (await res.json()) as PRReviewQueueResponse;
}
