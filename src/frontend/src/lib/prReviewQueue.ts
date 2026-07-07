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

/** Age for a calendar-time queue: minutes/hours/days/weeks since opened. */
export function ageSinceCreated(createdAt: string | null, now: number): string | null {
	if (!createdAt) return null;
	const created = Date.parse(createdAt);
	if (Number.isNaN(created)) return null;
	const deltaS = Math.max(0, Math.floor((now - created) / 1000));
	if (deltaS < 60) return 'just now';
	const minutes = Math.floor(deltaS / 60);
	if (minutes < 60) return `${minutes}m old`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `${hours}h old`;
	const days = Math.floor(hours / 24);
	if (days < 14) return `${days}d old`;
	const weeks = Math.floor(days / 7);
	return `${weeks}w ${days % 7}d old`;
}
