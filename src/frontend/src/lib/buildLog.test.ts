import { equal, ok } from 'node:assert/strict';
import { test } from 'node:test';
import { BUILD_LOG_ENTRIES, buildLogEntriesSorted, buildLogEntryBySlug } from './buildLog.ts';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

test('build log entries are unique, dated, and substantial', () => {
	const slugs = new Set<string>();
	for (const entry of BUILD_LOG_ENTRIES) {
		ok(!slugs.has(entry.slug), `duplicate build log slug: ${entry.slug}`);
		slugs.add(entry.slug);
		ok(DATE_RE.test(entry.date), `${entry.slug} has a non-ISO date: ${entry.date}`);
		ok(entry.summary.length > 40, `${entry.slug} has a thin summary`);
		ok(entry.measured.length > 10, `${entry.slug} has a thin "measured" clause`);
		ok(entry.body.length >= 1, `${entry.slug} has no body paragraphs`);
		ok(entry.links.length >= 1, `${entry.slug} carries no receipts`);
		for (const link of entry.links) {
			ok(/^https?:\/\//.test(link.url), `${entry.slug} link is not an absolute URL: ${link.url}`);
		}
	}
});

test('buildLogEntriesSorted is newest-first regardless of authoring order', () => {
	const sorted = buildLogEntriesSorted();
	for (let i = 1; i < sorted.length; i++) {
		ok(sorted[i - 1].date >= sorted[i].date, 'entries are not sorted newest-first');
	}
	equal(sorted.length, BUILD_LOG_ENTRIES.length);
});

test('buildLogEntryBySlug finds a known entry and rejects an unknown one', () => {
	const first = BUILD_LOG_ENTRIES[0];
	equal(buildLogEntryBySlug(first.slug)?.title, first.title);
	equal(buildLogEntryBySlug('does-not-exist'), undefined);
});
