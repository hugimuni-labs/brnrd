import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'MarkerNotice.svelte');
const generated = join(here, '.markerNotice.generated.mjs');

// Same rendering dance as WithheldNotice.test.ts / PublishConsentNotice.test.ts:
// compile the real component to a server target and render it with props,
// rather than string-matching the source.
async function renderNotice(props: {
	markerNotice: string | null;
	failureNotice: string | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'MarkerNotice'
	});
	writeFileSync(generated, compiled.js.code);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('neither notice renders nothing visible', async () => {
	const html = await renderNotice({ markerNotice: null, failureNotice: null });
	ok(!html.includes('marker'));
	ok(!html.includes('not a collaborator'));
});

test('a determined absence renders the server-owned sentence, naming the effective login', async () => {
	const html = await renderNotice({
		markerNotice:
			'brnrd-bot not a collaborator — assigns / review-requests / comment-tags addressed to it ' +
			"won't reach the resident; invite it in Settings → Collaborators.",
		failureNotice: null
	});
	ok(html.includes('brnrd-bot not a collaborator'));
	ok(html.includes('Settings → Collaborators'));
});

test('an unknown state (null) never renders the absence line — no guessed yes or no', async () => {
	// `markerNotice` is only ever non-null when the server positively proved
	// "not a collaborator" (see github_marker.marker_absence_text) — a
	// collaborator==true or ==None (unknown) repo passes null here, and both
	// must render nothing, not a downgraded warning.
	const html = await renderNotice({ markerNotice: null, failureNotice: null });
	ok(!html.includes('not a collaborator'));
});

test('a failure notice renders independently of the absence line', async () => {
	const html = await renderNotice({
		markerNotice: null,
		failureNotice: 'brnrd-bot collaborator check failed: 500 Server Error'
	});
	ok(html.includes('brnrd-bot collaborator check failed'));
	ok(!html.includes('not a collaborator'), 'a check failure is not a proven absence');
});

test('both notices render together, marker line first', async () => {
	const html = await renderNotice({
		markerNotice: 'brnrd-bot not a collaborator — invite it in Settings → Collaborators.',
		failureNotice: 'brnrd-bot invitation accept failed: 422 Unprocessable Entity'
	});
	const markerAt = html.indexOf('not a collaborator');
	const failureAt = html.indexOf('invitation accept failed');
	ok(markerAt !== -1 && failureAt !== -1 && markerAt < failureAt);
});
