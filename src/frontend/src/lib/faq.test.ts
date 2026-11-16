import { equal, ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { FAQ } from './faq.ts';

const here = dirname(fileURLToPath(import.meta.url));
const routePath = join(here, '..', 'routes', 'faq', '+page.svelte');
const generated = join(here, '.faqRoute.generated.mjs');

// Rendered, not string-matched: the HTML the visitor gets with JavaScript off.
async function renderRoute(): Promise<ReturnType<typeof render>> {
	const compiled = compile(readFileSync(routePath, 'utf8'), {
		generate: 'server',
		runes: true,
		name: 'FaqPage'
	});
	const runnable = compiled.js.code
		.replace(/'\$lib\/faq'/g, "'./faq.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default);
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

test('every question renders with its answer, openable and linkable, without JavaScript', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('<main'), 'no main landmark');
	ok(/<h1[\s>]/.test(html), 'no h1');
	for (const entry of FAQ) {
		ok(html.includes(`<details id="${entry.id}"`), `${entry.id} is not an anchored <details>`);
		ok(html.includes(`href="#${entry.id}"`), `${entry.id} has no link to itself`);
		ok(html.includes(entry.answer.slice(0, 40)), `${entry.id} answer did not render`);
	}
});

test('question ids are unique anchors', () => {
	const ids = FAQ.map((e) => e.id);
	equal(new Set(ids).size, ids.length);
	for (const id of ids) ok(/^[a-z][a-z0-9-]*$/.test(id), `${id} is not a clean anchor`);
});

test('the page makes none of the claims we measured we cannot make', () => {
	const text = FAQ.map((e) => `${e.question} ${e.answer}`).join('\n');
	ok(!/never forgets|no looking after|needs no looking/i.test(text));
	ok(!/\d[\d,.]*\s*(users|people|developers|teams)/i.test(text), 'a user count is on the page');
	ok(!/[€$]\s?\d/.test(text), 'a price figure is on the page; pricing owns it');
	const off = FAQ.find((e) => e.id === 'computer-off');
	ok(off && off.answer.startsWith('No.'), 'the computer-off answer must start with No.');
});

test('the safety answer names the guards the code has, not a blanket promise', () => {
	const safe = FAQ.find((e) => e.id === 'is-it-safe');
	ok(safe, 'no safety answer');
	ok(!/anything that cannot be undone/i.test(safe.answer));
	for (const guard of ['force-pushing', 'package', 'release', 'checks are not green']) {
		ok(safe.answer.includes(guard), `safety answer does not name ${guard}`);
	}
});
