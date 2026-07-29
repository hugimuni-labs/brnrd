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
