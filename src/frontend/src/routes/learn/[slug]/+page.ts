import { error } from '@sveltejs/kit';
import { SEARCH_TOPICS, searchTopicBySlug } from '$lib/searchTopics';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
	const topic = searchTopicBySlug(params.slug);
	if (!topic) error(404, 'Unknown technical note');
	return { topic };
};

export const entries = () => SEARCH_TOPICS.map(({ slug }) => ({ slug }));
