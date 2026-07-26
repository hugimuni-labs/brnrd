import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { isComplete } from './legalNotice.ts';

const here = dirname(fileURLToPath(import.meta.url));
const routePath = join(here, '..', 'routes', 'terms', '+page.svelte');
const generated = join(here, '.termsRoute.generated.mjs');

// Same rendering dance as legalNotice.test.ts: run the compiled route rather
// than string-match the source, so a claim that becomes false only in the
// rendered HTML (a link that silently disappears, a class typo swallowing an
// href) still fails this test.
//
// The route imports more of $lib than the legal-notice page does (#735 put
// the acceptance widget on it), so beyond the $app/paths swap: $lib modules
// are rewritten to their real files beside this test, and TermsGate — a
// component whose behaviour these tests do not assert — is stubbed with a
// no-op server component so the legal text renders without it.
async function renderRoute(): Promise<string> {
	const source = readFileSync(routePath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'TermsPage'
	});
	const runnable = compiled.js.code
		.replace(/'\$lib\/legalNotice'/g, "'./legalNotice.ts'")
		.replace(/'\$lib\/terms'/g, "'./terms.ts'")
		.replace(
			/import\s+TermsGate\s+from\s*'\$lib\/TermsGate\.svelte';/,
			'const TermsGate = () => {};'
		)
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

// Guards the sentence fixed alongside the legal pack landing (#569): /terms
// used to say a privacy notice "is in preparation" while /privacy did not
// exist. Once /privacy shipped that sentence became false with nothing
// enforcing the correction — this pins both halves so the claim can't rot
// back into a stale promise.
test('/terms does not claim the privacy notice is still in preparation', async () => {
	const html = await renderRoute();
	ok(!html.includes('is in preparation'));
});

test('/terms links to the published /privacy notice', async () => {
	const html = await renderRoute();
	ok(html.includes('href="/privacy"'));
});

// Same rot, second instance found while fixing the first: /terms also used to
// say the mentions légales notice "is not yet published" and "does not exist
// in this repository" — false the moment /legal-notice ships in the same PR.
// The claim is gated on legalNotice's own isComplete(), the same registry the
// notice renders from, so this only asserts the branch that matches today's
// real state.
test('/terms names the legal-notice publication state truthfully', async () => {
	const html = await renderRoute();
	if (isComplete()) {
		ok(html.includes('href="/legal-notice"'));
		ok(!html.includes('is not yet published'));
	} else {
		ok(!html.includes('href="/legal-notice"'));
		ok(html.includes('is not yet published'));
	}
});
