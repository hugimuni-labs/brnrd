import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { INSTALL_LINE } from './install.ts';
import { GITHUB_REPO } from './publicStats.ts';
import { hasCanonicalMeta } from './seo.ts';
import { cloudOnly } from './site.ts';

const here = dirname(fileURLToPath(import.meta.url));
let variant = 0;
async function renderRoute(meet = '', hours = '', video = '', configured = false) {
	const source = readFileSync(join(here, '../routes/booth/+page.svelte'), 'utf8')
		.replace(/const BOOTH = \{[\s\S]*?\};/, (match) =>
			configured ? match : `const BOOTH = ${JSON.stringify({ meet, hours })};`
		)
		.replace("const VIDEO_URL = '';", `const VIDEO_URL = ${JSON.stringify(video)};`);
	const compiled = compile(source, { generate: 'server', runes: true, name: 'BoothPage' });
	const generated = join(here, `.boothRoute.${process.pid}.${variant++}.generated.mjs`);
	const runnable = compiled.js.code
		.replace(/'\$lib\/install'/g, "'./install.ts'")
		.replace(/'\$lib\/publicStats'/g, "'./publicStats.ts'")
		.replace(/'\$lib\/seo'/g, "'./seo.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		return render((await import(generated)).default);
	} finally {
		rmSync(generated, { force: true });
	}
}

test('/booth renders the public pitch, pending demo, shared install and repo in order', async () => {
	const { body, head } = await renderRoute();
	const parts = [
		'The brnrd booth, virtual today',
		'The Web Summit venue is closed because of the storm, so the booth is here.',
		'brnrd turns the coding agent you already use (Claude Code, Codex…)',
		'Drop in for a live demo: ask it something yourself and watch it work',
		'Room link and hours here shortly',
		INSTALL_LINE,
		`https://github.com/${GITHUB_REPO}`
	];
	let last = -1;
	for (const part of parts) {
		const at = body.indexOf(part);
		ok(at > last, `missing or out of order: ${part}`);
		last = at;
	}
	ok(!body.includes('Join on Google Meet'));
	ok(!body.includes('<video'));
	ok(head.includes('property="og:title"'));
	ok(head.includes('name="description"'));
	ok(!cloudOnly('/booth'));
	ok(hasCanonicalMeta('/booth'));
});

test('demo needs both room and hours; optional trailer renders only when set', async () => {
	for (const [meet, hours] of [
		['https://meet.google.com/abc-defg-hij', ''],
		['', '14:00–16:00 Lisbon']
	]) {
		const { body } = await renderRoute(meet, hours);
		ok(body.includes('Room link and hours here shortly'));
		ok(!body.includes('Join on Google Meet'));
	}
	const { body } = await renderRoute(
		'https://meet.google.com/abc-defg-hij',
		'14:00–16:00 Lisbon',
		'https://example.com/trailer.mp4'
	);
	ok(body.includes('href="https://meet.google.com/abc-defg-hij"'));
	ok(body.includes('14:00–16:00 Lisbon'));
	ok(!body.includes('Room link and hours here shortly'));
	ok(body.includes('<video'));
	ok(body.includes('src="https://example.com/trailer.mp4"'));
});

test('configured booth renders the supplied session details', async () => {
	const { body } = await renderRoute('', '', '', true);
	ok(body.includes('href="https://meet.google.com/meet/ifp-ymqz-gaq"'));
	ok(body.includes('Today, 15:00–16:00 (Lisbon time)'));
	ok(!body.includes('Room link and hours here shortly'));
	ok(!body.includes('<video'));
});
