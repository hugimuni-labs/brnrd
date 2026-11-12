// One board per test file: the synthetic parts (fixture.mjs) behind parts-server, vite dev proxying /v1 to it, and a
// headless chromium on `/` (localhost, so the board mounts). Timing here is not a measure: §D7 is measured on a build.
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { fixture } from './fixture.mjs';
import { partsServer } from './parts-server.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));

export async function open({
	parts = fixture(),
	browserType = chromium,
	viewport = { width: 1440, height: 900 },
	consent = null,
	site = null,
	ready = '.brd .column.threadcol',
	persistent = false
} = {}) {
	const loom = await partsServer(parts, 0, consent, site);
	process.env.BRNRD_DEV_TARGET = `http://127.0.0.1:${loom.port}`;
	const vite = await createServer({
		root,
		logLevel: 'error',
		server: { port: 0, host: '127.0.0.1' }
	});
	await vite.listen();
	const base = `http://127.0.0.1:${vite.httpServer.address().port}/`;
	// a browser that is not installed must fail the test, not leave vite holding the process open:
	// on 2026-11-11 a WebKit launch on a Chromium-only runner hung main's CI for over an hour
	// WebKit's ephemeral contexts discard Cache API entries on navigation; restoration needs a profile.
	const profile = persistent ? await mkdtemp(join(tmpdir(), 'board-e2e-')) : null;
	const launch = profile
		? browserType.launchPersistentContext(profile, { viewport })
		: browserType.launch();
	const browser = await launch.catch(async (e) => {
		await vite.close();
		loom.server.closeAllConnections?.();
		loom.server.close();
		if (profile) await rm(profile, { recursive: true, force: true });
		throw e;
	});
	const page = profile ? await browser.newPage() : await browser.newPage({ viewport });
	const closeBrowser = async () => {
		await browser.close();
		if (profile) await rm(profile, { recursive: true, force: true });
	};
	const errors = [],
		requests = [];
	page.on('pageerror', (e) => errors.push(e.message));
	page.on('console', (m) => {
		if (m.type() === 'error' && m.text().startsWith('[board-render-error]')) errors.push(m.text());
	});
	// A Svelte boundary catches render exceptions before window.onerror. Count its fallback too,
	// including a transient fallback that immediately retries, so B.errors assertions stay meaningful.
	await page.addInitScript(() => {
		const seen = new WeakSet();
		const record = (node) => {
			if (!(node instanceof Element)) return;
			const found = [...node.querySelectorAll('[data-board-error]')];
			if (node.matches('[data-board-error]')) found.push(node);
			for (const e of found) {
				if (seen.has(e)) continue;
				seen.add(e);
				console.error('[board-render-error]', e.textContent);
			}
		};
		new MutationObserver((records) => {
			for (const r of records) for (const node of r.addedNodes) record(node);
		}).observe(document, { childList: true, subtree: true });
	});
	page.on('request', (r) => requests.push(new URL(r.url()).pathname));
	try {
		await page.goto(base);
		await page.waitForSelector(ready, { timeout: 60_000 });
	} catch (e) {
		// a page that never got ready must not leave its browser and servers holding the test process open
		await closeBrowser();
		await vite.close();
		loom.server.closeAllConnections?.();
		loom.server.close();
		throw e;
	}
	return {
		page,
		loom,
		errors,
		requests,
		base,
		browser,
		async close() {
			await closeBrowser();
			await vite.close();
			loom.server.closeAllConnections?.();
			loom.server.close();
		}
	};
}
