import { error } from '@sveltejs/kit';
import { BUILD_LOG_ENTRIES, buildLogEntryBySlug } from '$lib/buildLog';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
	const entry = buildLogEntryBySlug(params.slug);
	if (!entry) error(404, 'Unknown build log entry');
	return { entry };
};

export const entries = () => BUILD_LOG_ENTRIES.map(({ slug }) => ({ slug }));
