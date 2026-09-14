import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { BenchFile } from './bench.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'BenchLane.svelte');
const generated = join(here, '.benchLane.generated.mjs');

// Same compile-to-server-target dance as NewsLane.test.ts (no bundler in
// this test's toolchain) — see that file for the full rationale.
async function renderBench(props: { files?: BenchFile[]; error?: string | null }): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'BenchLane'
	});
	const runnable = compiled.js.code.replace(/'\.\/bench'/g, "'./bench.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				files: [],
				error: null,
				...props
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function file(overrides: Partial<BenchFile>): BenchFile {
	return {
		repo: 'Gurio__brr',
		place: 'src/brr/daemon.py',
		commit: 'abc1234567890',
		question: 'why does it retry',
		made_at: '2026-09-14T10:00:00Z',
		marks: [],
		path: 'Gurio__brr/src/brr/daemon.py/abc1234567890',
		...overrides
	};
}

test('a row renders the place, a short commit, and the question', async () => {
	const html = await renderBench({ files: [file({})] });
	ok(html.includes('src/brr/daemon.py'));
	ok(html.includes('abc123456789')); // commit truncated to 12 chars
	ok(html.includes('why does it retry'));
});

test('a row links to the bench page at repo/place/commit', async () => {
	const html = await renderBench({ files: [file({})] });
	ok(html.includes('href="/bench/Gurio__brr/src/brr/daemon.py/abc1234567890"'));
});

test('a row with no question renders none', async () => {
	const html = await renderBench({ files: [file({ question: null })] });
	ok(!html.includes('why does it retry'));
});

test('an error renders the message, not the list', async () => {
	const html = await renderBench({ files: [file({})], error: 'bench fetch failed: 500' });
	ok(html.includes('bench fetch failed: 500'));
	ok(!html.includes('src/brr/daemon.py'));
});
