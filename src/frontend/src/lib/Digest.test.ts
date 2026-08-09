import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'Digest.svelte');
const generated = join(here, '.digest.generated.mjs');

// The digest (design-run-route.md §The home page becomes a map, #1256):
// one "since you looked" aggregate line, always rendered once the feed
// resolves (unlike the retired strip's "hide at zero" — the digest is a
// glanceable fixture, not a nag), plus only the summons-worthy rows below
// it, plus one "caught up" action. Same compile-server-side dance as
// ControlStrip.test.ts / WarpBand.test.ts.
function renderDigest(props: {
	rows: Array<Record<string, unknown>> | null;
	now: number;
	lastLookedAt: number | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'Digest' });
	const runnable = compiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(generated, runnable);
	return import(`${generated}?t=${process.pid}-${Math.random()}`).then(
		(module) => render(module.default, { props }).body
	);
}

after(() => rmSync(generated, { force: true }));

const NOW = Date.parse('2026-08-09T20:00:00Z');

test('the feed not having resolved yet renders nothing (count doctrine)', async () => {
	const body = await renderDigest({ rows: null, now: NOW, lastLookedAt: null });
	ok(!body.includes('since'), 'no digest chrome while rows are still null');
	ok(!body.includes('role="status"'), 'no panel at all, not an empty one');
});

test('a resolved feed with nothing closed still renders the aggregate line', async () => {
	const body = await renderDigest({ rows: [], now: NOW, lastLookedAt: NOW - 3_600_000 });
	ok(
		body.includes('role="status"'),
		'the digest is a glanceable fixture, not a hide-at-zero strip'
	);
	ok(body.includes('0 runs'), 'an honest zero, never an omitted line');
	ok(body.includes('caught up'), 'the one action always renders');
});

test('never looked yet still renders an explicit since-instant, not a vague label', async () => {
	const body = await renderDigest({ rows: [], now: NOW, lastLookedAt: null });
	ok(body.includes('since'), 'the fallback window still names a concrete since-instant');
});

test('a summons-worthy run renders as a linked row; a quiet reply-only tick does not', async () => {
	const body = await renderDigest({
		rows: [
			{
				run_id: 'shipped-a-pr',
				name: 'the-fix',
				ended_at: new Date(NOW - 10 * 60_000).toISOString(),
				repo_label: 'Gurio/brr',
				bolt: 'accepted',
				external_refs: [{ kind: 'pr', number: 42 }]
			},
			{
				run_id: 'quiet-tick',
				ended_at: new Date(NOW - 5 * 60_000).toISOString(),
				repo_label: 'Gurio/brr',
				bolt: 'accepted',
				external_refs: [{ kind: 'reply', excerpt: 'nothing to do' }]
			}
		],
		now: NOW,
		lastLookedAt: NOW - 3_600_000
	});
	ok(body.includes('the-fix'), 'the summons-worthy run renders');
	ok(body.includes('/runs/Gurio__brr/shipped-a-pr'), 'linked to its run node');
	ok(!body.includes('quiet-tick'), 'a bare reply-only tick folds in silently, no row');
});

test('dissent renders its verdict label on the row', async () => {
	const body = await renderDigest({
		rows: [
			{
				run_id: 'annotated-run',
				name: 'the-overrule',
				ended_at: new Date(NOW - 10 * 60_000).toISOString(),
				repo_label: 'Gurio/brr',
				bolt: 'annotated',
				external_refs: []
			}
		],
		now: NOW,
		lastLookedAt: NOW - 3_600_000
	});
	ok(body.includes('accepted — with dissent'), 'the dissent verdict label renders');
});

test('no per-row TAKE control exists anywhere on the digest', async () => {
	const body = await renderDigest({
		rows: [
			{
				run_id: 'shipped-a-pr',
				name: 'the-fix',
				ended_at: new Date(NOW - 10 * 60_000).toISOString(),
				repo_label: 'Gurio/brr',
				bolt: 'accepted',
				external_refs: [{ kind: 'pr', number: 42 }]
			}
		],
		now: NOW,
		lastLookedAt: NOW - 3_600_000
	});
	ok(!/>\s*take\s*</.test(body), 'per-row TAKE died with the strip and the lane');
	ok(!body.includes('take all'), 'TAKE ALL died too');
});
