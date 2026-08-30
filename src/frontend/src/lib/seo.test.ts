import { equal, ok } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { SEARCH_TOPICS } from './searchTopics.ts';
import { canonicalUrl, isIndexablePath, normalizePathname } from './seo.ts';

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, '..', '..', 'static');

test('canonical paths are normalized to the brnrd.dev origin', () => {
	equal(normalizePathname('/learn//agent-orchestration/'), '/learn/agent-orchestration');
	equal(canonicalUrl('/'), 'https://brnrd.dev/');
	equal(canonicalUrl('/pricing/'), 'https://brnrd.dev/pricing');
});

test('only intentional public surfaces are indexable', () => {
	for (const path of ['/', '/pricing', '/terms', '/privacy', '/legal-notice', '/learn']) {
		ok(isIndexablePath(path), `${path} should be indexable`);
	}
	for (const path of ['/login', '/new', '/daily', '/garage', '/connect', '/brand-bench', '/ascii']) {
		ok(!isIndexablePath(path), `${path} should remain out of search inventory`);
	}
	for (const topic of SEARCH_TOPICS) {
		ok(isIndexablePath(`/learn/${topic.slug}`), `${topic.slug} should be indexable`);
	}
});

test('search topics are unique and substantial enough to be useful pages', () => {
	const slugs = new Set<string>();
	for (const topic of SEARCH_TOPICS) {
		ok(!slugs.has(topic.slug), `duplicate search topic slug: ${topic.slug}`);
		slugs.add(topic.slug);
		ok(topic.lede.length > 140, `${topic.slug} has a thin lede`);
		ok(topic.sections.length >= 2, `${topic.slug} needs at least two substantive sections`);
		ok(topic.sections.every((section) => section.paragraphs.length >= 2), `${topic.slug} has a thin section`);
	}
});

test('robots advertises the sitemap and the sitemap covers every search topic', () => {
	const robots = readFileSync(join(staticDir, 'robots.txt'), 'utf8');
	const sitemap = readFileSync(join(staticDir, 'sitemap.xml'), 'utf8');
	ok(robots.includes('Sitemap: https://brnrd.dev/sitemap.xml'));
	ok(sitemap.includes('<loc>https://brnrd.dev/</loc>'));
	ok(sitemap.includes('<loc>https://brnrd.dev/pricing</loc>'));
	for (const topic of SEARCH_TOPICS) {
		ok(
			sitemap.includes(`<loc>https://brnrd.dev/learn/${topic.slug}</loc>`),
			`sitemap missing ${topic.slug}`
		);
	}
});
