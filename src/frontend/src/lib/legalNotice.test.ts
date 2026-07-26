import { equal, ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import {
	LEGAL_NOTICE_FIELDS,
	PENDING,
	type LegalField,
	displayValue,
	isComplete,
	pendingFields,
	pendingReport
} from './legalNotice.ts';

const here = dirname(fileURLToPath(import.meta.url));
const routePath = join(here, '..', 'routes', 'legal-notice', '+page.svelte');

// Rendering the real route, not reading it. A page that correctly *removed* a
// placeholder would still contain the word "placeholder" in the module that
// enforces it, so string-matching the source proves nothing; the assertions
// below run against the HTML the component actually emits.
//
// The dance with a temp file is module resolution, not cleverness: the compiled
// server component imports `svelte/internal/server` and `$lib/legalNotice`, so it
// has to sit inside src/lib for node to resolve either, and `$app/paths` only
// exists inside a SvelteKit build so it is swapped for the identity function it
// is at this app's root base path. Dot-prefixed so a stray leftover is invisible
// to prettier and eslint.
async function renderRoute(): Promise<string> {
	const source = readFileSync(routePath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'LegalNoticePage'
	});
	const generated = join(here, '.legalNoticeRoute.generated.mjs');
	const runnable = compiled.js.code
		.replace(/'\$lib\/legalNotice'/g, "'./legalNotice.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(join(here, '.legalNoticeRoute.generated.mjs'), { force: true }));

test('the /legal-notice route renders the publisher and the host it is served from', async () => {
	const html = await renderRoute();
	ok(html.includes('Mentions légales'));
	ok(html.includes('HugiMuni SAS'));
	ok(html.includes('Éditeur du site'));
	// Sourced from Upsun's own impressum + the government register, not guessed.
	ok(html.includes('Platform.sh SAS (Upsun)'));
	ok(html.includes('22 rue de Palestro, 75002 Paris, France'));
	ok(html.includes('+33 (0)1 40 09 30 00'));
});

test('the /legal-notice route links to /terms', async () => {
	const html = await renderRoute();
	ok(html.includes('href="/terms"'));
});

test('unknown identifiers render as the visible placeholder, and say so loudly', async () => {
	const html = await renderRoute();
	const missing = pendingFields();
	if (missing.length === 0) {
		// The K-bis values have landed: no placeholder, no warning banner.
		ok(!html.includes(PENDING));
		ok(!html.includes('ne pas publier'));
		return;
	}
	ok(html.includes(PENDING));
	ok(html.includes('ne pas publier'));
	// Every pending field still shows its label, so the page reads as a form
	// with holes rather than as a notice that silently omits statutory fields.
	for (const field of missing) ok(html.includes(field.fr), `label missing for ${field.key}`);
});

function withValues(fields: readonly LegalField[]): LegalField[] {
	return fields.map((field) => ({ ...field, value: field.value ?? `filled:${field.key}` }));
}

function blankOut(fields: readonly LegalField[], keys: string[]): LegalField[] {
	return withValues(fields).map((field) =>
		keys.includes(field.key) ? { ...field, value: null } : field
	);
}

test('the completeness check passes once every field carries a value', () => {
	const filled = withValues(LEGAL_NOTICE_FIELDS);
	equal(pendingFields(filled).length, 0);
	equal(isComplete(filled), true);
	equal(pendingReport(filled), '');
	// And the page would then show values rather than the placeholder.
	ok(filled.every((field) => displayValue(field) !== PENDING));
});

test('the completeness check fails while a field is empty, and names which', () => {
	const holes = blankOut(LEGAL_NOTICE_FIELDS, ['publisher.siren', 'publisher.registeredOffice']);
	equal(isComplete(holes), false);
	equal(
		pendingFields(holes)
			.map((field) => field.key)
			.join(','),
		'publisher.siren,publisher.registeredOffice'
	);
	const report = pendingReport(holes);
	ok(report.includes('publisher.siren'));
	ok(report.includes('publisher.registeredOffice'));
	ok(report.includes('SIREN number (9 digits)'));
	// The fields that *are* filled stay out of the maintainer's todo.
	ok(!report.includes('publisher.shareCapital'));
	ok(report.includes('2 statutory field(s)'));
});

test('a pending field never renders as something mistakable for data', () => {
	const pending = { ...LEGAL_NOTICE_FIELDS[0], value: null };
	equal(displayValue(pending), PENDING);
	// Not empty, not a dash, not a number.
	ok(PENDING.trim().length > 0);
	ok(!/\d/.test(PENDING));
});

// The gate. This is the check that must fail while any statutory field is
// missing — it is expected to be RED until the maintainer fills the K-bis
// values into src/frontend/src/lib/legalNotice.ts, and turning it green is the
// signal that /legal-notice may be published. Its message is the todo list.
test('/legal-notice carries every statutory field LCEN art. 6-III requires', () => {
	ok(isComplete(), pendingReport());
});
