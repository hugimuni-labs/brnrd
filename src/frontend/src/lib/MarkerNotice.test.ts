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
	status: 'permission-missing' | 'not-a-collaborator' | 'check-unavailable' | 'unknown' | null;
	botLogin?: string;
	repoFullName?: string;
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
	const html = await renderNotice({ status: null });
	ok(!html.includes('marker'));
	ok(!html.includes('not a collaborator'));
});

test('a determined absence renders the class-owned remedy, naming the effective login', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: 'brnrd-bot'
	});
	ok(html.includes('brnrd-bot not a collaborator'));
	ok(html.includes('Settings → Collaborators'));
});

test('unknown renders as unknown rather than guessing yes or no', async () => {
	const html = await renderNotice({ status: 'unknown' });
	ok(html.includes('collaborator status unknown'));
	ok(!html.includes('not a collaborator'));
});

test('permission-missing renders the named remedy and never transport copy', async () => {
	const html = await renderNotice({ status: 'permission-missing' });
	ok(html.includes('permission missing'));
	ok(html.includes('Administration: read'));
	ok(!html.includes('403 Forbidden'));
	ok(!html.includes('developer.mozilla.org'));
});

test('check-unavailable renders a retry remedy', async () => {
	const html = await renderNotice({ status: 'check-unavailable' });
	ok(html.includes('collaborator check unavailable'));
	ok(html.includes('try again later'));
});

// #885 additions: click-to-copy bot handle + a link to the repo's GitHub
// collaborators page, both gated on the `not-a-collaborator` class.

test('a determined absence with a bot login renders the copy-handle control', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: 'brnrd-bot',
		repoFullName: ''
	});
	ok(html.includes('copy @brnrd-bot'));
});

test('a determined absence with a repo name renders the collaborators-page link', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: '',
		repoFullName: 'Gurio/brr'
	});
	ok(html.includes('open collaborators page'));
	ok(html.includes('https://github.com/Gurio/brr/settings/access'));
});

test('an empty botLogin/repoFullName renders neither addition, even with a firing status', async () => {
	const html = await renderNotice({
		status: 'not-a-collaborator',
		botLogin: '',
		repoFullName: ''
	});
	ok(!html.includes('copy @'));
	ok(!html.includes('open collaborators page'));
});

test('a null status renders neither addition, even when bot login and repo name are given', async () => {
	const html = await renderNotice({
		status: null,
		botLogin: 'brnrd-bot',
		repoFullName: 'Gurio/brr'
	});
	ok(!html.includes('copy @'));
	ok(!html.includes('open collaborators page'));
});
