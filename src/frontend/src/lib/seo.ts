export const SITE_ORIGIN = 'https://brnrd.dev';
export const HOME_TITLE = 'brnrd — persistent coding agents on your machine';
export const HOME_DESCRIPTION =
	'brnrd is an open-source resident layer for persistent coding agents. Keep context between runs, route work from GitHub and messaging surfaces, and execute through the agent CLI already on your machine.';
export const SOCIAL_IMAGE = `${SITE_ORIGIN}/brr-banner.png`;

const INDEXABLE_EXACT_PATHS = new Set([
	'/',
	'/pricing',
	'/terms',
	'/privacy',
	'/legal-notice',
	'/learn',
	'/log'
]);

const INDEXABLE_PREFIXES = ['/learn/', '/log/'];

// Real public pages that are not in the sitemap / not meant for search
// indexing (an editorial choice — see robots.txt + sitemap.xml, which name
// only the paths above), but that are still shared as standalone links and
// need a correct canonical + og:url so an unfurler resolves them to
// themselves rather than rendering with no canonical at all.
const CANONICAL_ONLY_PATHS = new Set(['/sub-processors', '/beta-hosted-execution']);

export function normalizePathname(pathname: string): string {
	if (!pathname || pathname === '/') return '/';
	return `/${pathname.split('/').filter(Boolean).join('/')}`;
}

export function isIndexablePath(pathname: string): boolean {
	const normalized = normalizePathname(pathname);
	return (
		INDEXABLE_EXACT_PATHS.has(normalized) ||
		INDEXABLE_PREFIXES.some((prefix) => normalized.startsWith(prefix))
	);
}

// Broader than isIndexablePath: every route that owns real per-page SEO
// content and should carry a canonical link + og:url, whether or not it is
// also in the sitemap. isIndexablePath still gates the robots directive
// (index,follow vs noindex,nofollow) — that is the separate, deliberate
// search-indexing policy the sitemap encodes.
export function hasCanonicalMeta(pathname: string): boolean {
	const normalized = normalizePathname(pathname);
	return isIndexablePath(normalized) || CANONICAL_ONLY_PATHS.has(normalized);
}

export function canonicalUrl(pathname: string): string {
	const normalized = normalizePathname(pathname);
	return normalized === '/' ? `${SITE_ORIGIN}/` : `${SITE_ORIGIN}${normalized}`;
}
