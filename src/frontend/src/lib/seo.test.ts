import { equal, ok } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { SEARCH_TOPICS } from './searchTopics.ts';
import { canonicalUrl, hasCanonicalMeta, isIndexablePath, normalizePathname } from './seo.ts';

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, '..', '..', 'static');

const publicPaths = ['/', '/pricing', '/terms', '/privacy', '/legal-notice', '/learn'];

test('canonical paths are normalized to the brnrd.dev origin', () => {
	equal(normalizePathname('/learn//agent-orchestration/'), '/learn/agent-orchestration');
	equal(canonicalUrl('/'), 'https://brnrd.dev/');
	equal(canonicalUrl('/pricing/'), 'https://brnrd.dev/pricing');
});

test('only intentional public surfaces are indexable', () => {
	for (const path of publicPaths) {
		ok(isIndexablePath(path), `${path} should be indexable`);
	}
	for (const path of [
		'/login',
		'/new',
		'/daily',
		'/garage',
		'/connect',
		'/brand-bench',
		'/ascii'
	]) {
		ok(!isIndexablePath(path), `${path} should remain out of search inventory`);
	}
	for (const topic of SEARCH_TOPICS) {
		ok(isIndexablePath(`/learn/${topic.slug}`), `${topic.slug} should be indexable`);
	}
});

test('sub-processors and beta-hosted-execution get canonical + og:url without joining search inventory', () => {
	for (const path of ['/sub-processors', '/beta-hosted-execution']) {
		ok(hasCanonicalMeta(path), `${path} should carry a canonical + og:url`);
		ok(!isIndexablePath(path), `${path} should stay out of the sitemap / robots index`);
	}
	for (const path of publicPaths) {
		ok(hasCanonicalMeta(path), `${path} should still carry a canonical + og:url`);
	}
});

test('search topics are unique and substantial enough to be useful pages', () => {
	const slugs = new Set<string>();
	for (const topic of SEARCH_TOPICS) {
		ok(!slugs.has(topic.slug), `duplicate search topic slug: ${topic.slug}`);
		slugs.add(topic.slug);
		ok(topic.lede.length > 140, `${topic.slug} has a thin lede`);
		ok(topic.sections.length >= 2, `${topic.slug} needs at least two substantive sections`);
		ok(
			topic.sections.every((section) => section.paragraphs.length >= 2),
			`${topic.slug} has a thin section`
		);
	}
});

test('robots advertises the sitemap and the sitemap covers all public search inventory', () => {
	const robots = readFileSync(join(staticDir, 'robots.txt'), 'utf8');
	const sitemap = readFileSync(join(staticDir, 'sitemap.xml'), 'utf8');
	ok(robots.includes('Sitemap: https://brnrd.dev/sitemap.xml'));
	for (const path of publicPaths) {
		const url = path === '/' ? 'https://brnrd.dev/' : `https://brnrd.dev${path}`;
		ok(sitemap.includes(`<loc>${url}</loc>`), `sitemap missing ${path}`);
	}
	for (const topic of SEARCH_TOPICS) {
		ok(
			sitemap.includes(`<loc>https://brnrd.dev/learn/${topic.slug}</loc>`),
			`sitemap missing ${topic.slug}`
		);
	}
});
