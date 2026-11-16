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
	ok(
		!/\b(at|from) (the|our) (stand|booth)\b/i.test(text),
		'the stand and booth are not on the page'
	);
	ok(!/picks? up where it was/i.test(text), 'a run cut off by sleep does not resume where it was');
});

test('the old computer-off anchor still lands on the laptop question', async () => {
	const { body: html } = await renderRoute();
	equal(FAQ.find((e) => e.id === 'laptop-open')?.alias, 'computer-off');
	ok(html.includes('<span id="computer-off"></span>'), 'alias anchor missing');
});

test('the safety answer says what the seed tells it, not a list of guards or a blanket promise', () => {
	const safe = FAQ.find((e) => e.id === 'is-it-safe');
	ok(safe, 'no safety answer');
	ok(!/anything that cannot be undone|before steps it cannot take back/i.test(safe.answer));
	ok(/within what you have allowed/.test(safe.answer));
	ok(/can still make mistakes/.test(safe.answer));
	ok(
		!/package|checks are not green|determined workaround/.test(safe.answer),
		'a guard list is back'
	);
});
