import { error } from '@sveltejs/kit';
import { BUILD_LOG_ENTRIES, buildLogEntryBySlug } from '$lib/buildLog';
import type { PageLoad } from './$types';

// See routes/pricing/+page.ts for the rationale: this route never touches
// authState, so it is safe to override the root layout's `ssr = false` and
// bake its head into a prerendered file. `entries` below already enumerates
// every slug, which is what lets the prerenderer crawl them without a
// running server — without this override, adapter-static does not even
// emit a static file for this route at all (confirmed by reverting the
// override and rebuilding: no `log.html`, no `log/<slug>.html` — the
// sitemap.xml entries would 404 in production; found reviewing #1758,
// before merge).
export const prerender = true;
export const ssr = true;

export const load: PageLoad = ({ params }) => {
	const entry = buildLogEntryBySlug(params.slug);
	if (!entry) error(404, 'Unknown build log entry');
	return { entry };
};

export const entries = () => BUILD_LOG_ENTRIES.map(({ slug }) => ({ slug }));
