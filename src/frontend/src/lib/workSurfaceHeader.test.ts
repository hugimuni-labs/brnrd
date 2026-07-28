import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { SurfaceResponse } from './surface.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'WorkSurface.svelte');
const generated = join(here, '.workSurface.generated.mjs');

// Same rendering dance as WithheldNotice.test.ts: compile the real component
// and render it with real props, so the header's own condition (not a
// restated copy of it) is what gets asserted against. `MarkdownContent` and
// `WithheldNotice` are stubbed the same way termsPrivacyLink.test.ts stubs
// TermsGate — this test does not exercise either branch that renders them
// (files is empty and unwithheld), and neither compiles standalone outside a
// bundler's `.svelte` resolution.
async function renderSurface(data: SurfaceResponse): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'WorkSurface'
	});
	const runnable = compiled.js.code
		.replace(
			/import\s+MarkdownContent\s+from\s*'\.\/MarkdownContent\.svelte';/,
			'const MarkdownContent = () => {};'
		)
		.replace(
			/import\s+WithheldNotice\s+from\s*'\.\/WithheldNotice\.svelte';/,
			'const WithheldNotice = () => {};'
		)
		.replace(/'\.\/transitions'/g, "'./transitions.ts'")
		.replace(/'\.\/surface'/g, "'./surface.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { data } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

// The header's timestamp span (`text-ink-mute`) only — isolated so this test
// can't pass by accident against the unrelated "No corpus mirrored yet."
// empty-state sentence, which contains the same substring for a different
// reason (no mirror has ever happened at all, not "not this tick").
function headerTimestampText(html: string): string {
	const match = /text-ink-mute">([^<]*)</.exec(html);
	if (!match) throw new Error('header timestamp span not found in rendered output');
	return match[1];
}

// The bug this pins: the server stamps `reported_at` on every publish tick,
// even one that accepted zero files — so the header used to say "mirrored
// <ts>" directly above a body that said "No corpus mirrored yet.", two
// statements from the same response contradicting each other. The timestamp
// is real information (a last-contact time); only the verb claiming a mirror
// happened was wrong.
test('an empty corpus does not claim to have been "mirrored" at the reported time', async () => {
	const html = await renderSurface({
		generated_at: '2026-07-27T22:43:13Z',
		files: [],
		reported_at: '2026-07-27T22:43:13Z'
	});
	const header = headerTimestampText(html);
	ok(!header.includes('mirrored'), 'zero files were accepted — nothing was mirrored');
	ok(
		header.includes('last checked'),
		'the real timestamp should still be visible, honestly labeled'
	);
});

test('a non-empty corpus keeps saying "mirrored" at the reported time', async () => {
	const html = await renderSurface({
		generated_at: '2026-07-27T22:43:13Z',
		files: [{ path: 'surface/index.md', markdown: '# hi' }],
		reported_at: '2026-07-27T22:43:13Z'
	});
	const header = headerTimestampText(html);
	ok(header.includes('mirrored'));
	ok(!header.includes('last checked'));
});
