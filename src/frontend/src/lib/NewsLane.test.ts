import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { NewsItem } from './news.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'NewsLane.svelte');
const generated = join(here, '.newsLane.generated.mjs');

// Same compile-to-server-target dance as ConfigRequests.test.ts (no bundler
// in this test's toolchain) — see that file for the full rationale.
async function renderNews(props: { items?: NewsItem[]; error?: string | null }): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'NewsLane'
	});
	const runnable = compiled.js.code
		.replace(/'\.\/news'/g, "'./news.ts'")
		.replace(/'\.\/statusPalette'/g, "'./statusPalette.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				items: [],
				error: null,
				...props
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function item(overrides: Partial<NewsItem>): NewsItem {
	return {
		kind: 'release',
		subject: 'pypi',
		prior: '0.6.18',
		current: '0.7.0',
		source: 'https://pypi.org/pypi/brnrd/json',
		expires_at: null,
		daemon_reported_at: '2026-09-01T12:00:00Z',
		daemon_stale: false,
		...overrides
	};
}

test('a release item renders the update-available line', async () => {
	const html = await renderNews({ items: [item({})] });
	ok(html.includes('pypi update available: 0.6.18 → 0.7.0'));
});

test('an item with no prior renders the bare current-value line', async () => {
	const html = await renderNews({
		items: [item({ kind: 'model', subject: 'claude-opus-5', prior: null, current: 'available' })]
	});
	ok(html.includes('claude-opus-5: available'));
});

test('an expiring item names its retirement date', async () => {
	const html = await renderNews({
		items: [
			item({
				kind: 'core-retirement',
				subject: 'gpt-5-codex',
				current: 'pinned',
				expires_at: '2026-12-01'
			})
		]
	});
	ok(html.includes('gpt-5-codex: pinned (retires 2026-12-01)'));
});

test('a stale report is marked, not hidden', async () => {
	const html = await renderNews({ items: [item({ daemon_stale: true })] });
	ok(html.includes('stale'));
	ok(html.includes('pypi update available'));
});

test('a fetch error renders instead of the rows, not alongside them', async () => {
	const html = await renderNews({ items: [item({})], error: 'news fetch failed: 500' });
	ok(html.includes('news fetch failed: 500'));
	ok(!html.includes('update available'));
});
