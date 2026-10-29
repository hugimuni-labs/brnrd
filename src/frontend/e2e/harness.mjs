// One board per test file: the synthetic parts (fixture.mjs) behind parts-server, vite dev proxying /v1 to it, and a
// headless chromium on `/` (localhost, so the board mounts). Timing here is not a measure: §D7 is measured on a build.
import { createServer } from 'vite';
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { fixture } from './fixture.mjs';
import { partsServer } from './parts-server.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));

export async function open({
	parts = fixture(),
	viewport = { width: 1440, height: 900 },
	consent = null,
	ready = '.brd .column.threadcol'
} = {}) {
	const loom = await partsServer(parts, 0, consent);
	process.env.BRNRD_DEV_TARGET = `http://127.0.0.1:${loom.port}`;
	const vite = await createServer({
		root,
		logLevel: 'error',
		server: { port: 0, host: '127.0.0.1' }
	});
	await vite.listen();
	const base = `http://127.0.0.1:${vite.httpServer.address().port}/`;
	const browser = await chromium.launch();
	const page = await browser.newPage({ viewport });
	const errors = [],
		requests = [];
	page.on('pageerror', (e) => errors.push(e.message));
	page.on('request', (r) => requests.push(new URL(r.url()).pathname));
	await page.goto(base);
	await page.waitForSelector(ready, { timeout: 60_000 });
	return {
		page,
		loom,
		errors,
		requests,
		base,
		browser,
		async close() {
			await browser.close();
			await vite.close();
			loom.server.closeAllConnections?.();
			loom.server.close();
		}
	};
}
