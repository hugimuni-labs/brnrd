// Public counters for the landing surface (#509). Two sources, both
// anonymous: `GET /v1/stats/public` (accounts + supporter cohort, coarse
// totals only, 60s server cache) and the GitHub repo API for stars/forks
// (unauthenticated, 60 req/h — one fetch per page load, no polling).

// Repo the star counter points at. Single constant on purpose: the
// Gurio/brr → hugimuni-labs/brnrd transfer rewrites exactly this line.
export const GITHUB_REPO = 'Gurio/brr';

export interface PublicStats {
	accounts: number;
	supporter_seats_total: number;
	supporter_seats_taken: number;
}

export interface RepoStats {
	stars: number;
	forks: number;
}

export async function fetchPublicStats(fetcher: typeof fetch = fetch): Promise<PublicStats | null> {
	try {
		const resp = await fetcher('/v1/stats/public');
		if (!resp.ok) return null;
		const data = await resp.json();
		if (typeof data?.accounts !== 'number') return null;
		return data as PublicStats;
	} catch {
		// Counters are decoration on the landing, never a gate: any failure
		// renders as absence, not as an error state the visitor must read.
		return null;
	}
}

export async function fetchRepoStats(fetcher: typeof fetch = fetch): Promise<RepoStats | null> {
	try {
		const resp = await fetcher(`https://api.github.com/repos/${GITHUB_REPO}`, {
			headers: { Accept: 'application/vnd.github+json' }
		});
		if (!resp.ok) return null;
		const data = await resp.json();
		if (typeof data?.stargazers_count !== 'number') return null;
		return { stars: data.stargazers_count, forks: data.forks_count ?? 0 };
	} catch {
		return null;
	}
}
