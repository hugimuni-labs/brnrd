import { error } from '@sveltejs/kit';
import { SEARCH_TOPICS, searchTopicBySlug } from '$lib/searchTopics';
import type { PageLoad } from './$types';

// See routes/pricing/+page.ts for the rationale: this route never touches
// authState, so it is safe to override the root layout's `ssr = false` and
// bake its head into a prerendered file. `entries` below already enumerates
// every slug, which is what lets the prerenderer crawl them without a
// running server.
export const prerender = true;
export const ssr = true;

export const load: PageLoad = ({ params }) => {
	const topic = searchTopicBySlug(params.slug);
	if (!topic) error(404, 'Unknown technical note');
	return { topic };
};

export const entries = () => SEARCH_TOPICS.map(({ slug }) => ({ slug }));
