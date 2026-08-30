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
	'/learn'
]);

export function normalizePathname(pathname: string): string {
	if (!pathname || pathname === '/') return '/';
	return `/${pathname.split('/').filter(Boolean).join('/')}`;
}

export function isIndexablePath(pathname: string): boolean {
	const normalized = normalizePathname(pathname);
	return INDEXABLE_EXACT_PATHS.has(normalized) || normalized.startsWith('/learn/');
}

export function canonicalUrl(pathname: string): string {
	const normalized = normalizePathname(pathname);
	return normalized === '/' ? `${SITE_ORIGIN}/` : `${SITE_ORIGIN}${normalized}`;
}
