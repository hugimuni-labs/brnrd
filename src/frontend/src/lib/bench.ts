// The bench (design-the-loom.md §6, §18): the weaver's draft layer under
// the kb — one markdown file per (repo, place, commit), listed and
// rendered from `GET /v1/dashboard/bench(/{repo}/{place}/{commit})`
// (`src/brnrd/routers/dashboard.py`, `src/brnrd/bench_store.py`). A fold
// that mattered is promoted to a kb page by a user mark; nothing here
// writes a mark — this module only lists and reads.

// `ResolvedPathname` is a type-only import (costs nothing at runtime, and
// stays resolvable under plain `node --test`, unlike a *value* import of
// `resolve()` from `$app/paths`, which only exists inside SvelteKit's Vite
// build) — same idiom `runNode.ts`'s `runNodeHref` uses for the same reason.
import type { ResolvedPathname } from '$app/types';

export interface BenchFile {
	repo: string;
	place: string;
	commit: string;
	question: string | null;
	made_at: string | null;
	marks: string[];
	path: string;
}

export interface BenchFileDetail extends BenchFile {
	body: string;
}

export interface BenchResponse {
	files: BenchFile[];
}

export class BenchAuthError extends Error {}

/** Fetches the account-scoped bench listing. Throws `BenchAuthError` on a
 * 401 (no session cookie), same shape as the other dashboard fetchers. */
export async function fetchBench(fetchImpl: typeof fetch = fetch): Promise<BenchResponse> {
	const res = await fetchImpl('/v1/dashboard/bench', { credentials: 'include' });
	if (res.status === 401) {
		throw new BenchAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`bench fetch failed: ${res.status}`);
	}
	return (await res.json()) as BenchResponse;
}

/** Fetches one bench file's frontmatter + body. `null` on a 404 (the file
 * was marked, moved, or never existed) — distinct from the auth/network
 * failures, which still throw. */
export async function fetchBenchFile(
	repo: string,
	place: string,
	commit: string,
	fetchImpl: typeof fetch = fetch
): Promise<BenchFileDetail | null> {
	const res = await fetchImpl(benchApiPath(repo, place, commit), { credentials: 'include' });
	if (res.status === 401) {
		throw new BenchAuthError('not signed in');
	}
	if (res.status === 404) {
		return null;
	}
	if (!res.ok) {
		throw new Error(`bench file fetch failed: ${res.status}`);
	}
	return (await res.json()) as BenchFileDetail;
}

function encodedSegments(place: string): string {
	return place
		.split('/')
		.filter((part) => part.length > 0)
		.map(encodeURIComponent)
		.join('/');
}

function benchApiPath(repo: string, place: string, commit: string): string {
	return `/v1/dashboard/bench/${encodeURIComponent(repo)}/${encodedSegments(place)}/${encodeURIComponent(commit)}`;
}

/** Route to the bench page for one file — `/bench/[repo]/[...rest]`, `rest`
 * being `<place>/<commit>` (the store's own nested-dir layout, split from
 * the right server-side too — see `dashboard_bench_file_api`'s doc). */
export function benchFileHref(repo: string, place: string, commit: string): ResolvedPathname {
	return `/bench/${encodeURIComponent(repo)}/${encodedSegments(place)}/${encodeURIComponent(commit)}` as ResolvedPathname;
}

/** Age label for the bench row list — `made_at` is a plain ISO timestamp,
 * not the daemon-report freshness clock `_age_label` elsewhere reads, so
 * this stays a small local helper rather than importing that one's
 * assumptions about what "stale" means for a fold. */
export function benchAgeLabel(madeAt: string | null, now: Date = new Date()): string {
	if (!madeAt) return '';
	const then = new Date(madeAt);
	if (Number.isNaN(then.getTime())) return '';
	const seconds = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 1000));
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `${hours}h`;
	const days = Math.floor(hours / 24);
	return `${days}d`;
}
