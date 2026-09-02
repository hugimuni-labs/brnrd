// Drive the /log index and one permalink at 390 and 1440 — a public,
// indexable surface, so it gets looked at, not reasoned about.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const PORT = 5193;
const OUT = process.env.REPRO_OUT ?? '/tmp/log-drive';

async function waitForServer(url, tries = 90) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok || res.status === 404) return;
		} catch {
			/* not up yet */
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

async function main() {
	const fs = await import('node:fs/promises');
	await fs.mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe'],
		cwd: new URL('..', import.meta.url).pathname
	});
	vite.stderr.on('data', (d) => process.stderr.write(String(d)));
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		for (const vp of [
			{ w: 390, h: 844, tag: '390' },
			{ w: 1440, h: 900, tag: '1440' }
		]) {
			for (const path of ['/log', '/log/retired-codex-models-still-selectable']) {
				const context = await browser.newContext({ viewport: { width: vp.w, height: vp.h } });
				const page = await context.newPage();
				const errors = [];
				page.on('pageerror', (e) => errors.push(String(e)));
				const resp = await page.goto(`http://localhost:${PORT}${path}`, {
					waitUntil: 'networkidle'
				});
				await delay(5200);
				const overflow = await page.evaluate(
					() => document.documentElement.scrollWidth > window.innerWidth
				);
				const ld = await page.evaluate(() => {
					const el = document.querySelector('script[type="application/ld+json"]');
					if (!el) return null;
					try {
						return Object.keys(JSON.parse(el.textContent ?? '{}')).join(',');
					} catch (e) {
						return `UNPARSEABLE: ${e}`;
					}
				});
				const canonical = await page.evaluate(
					() => document.querySelector('link[rel=canonical]')?.getAttribute('href') ?? null
				);
				const robots = await page.evaluate(
					() => document.querySelector('meta[name=robots]')?.getAttribute('content') ?? null
				);
				const title = await page.title();
				const tag = `${vp.tag}${path.replace(/\//g, '_')}`;
				console.log(
					`[${vp.tag} ${path}] status=${resp?.status()} hOverflow=${overflow} jsonld=${ld} canonical=${canonical} robots=${robots} pageerrors=${errors.length}`
				);
				console.log(`   title: ${title}`);
				await page.screenshot({ path: `${OUT}/${tag}.png`, fullPage: vp.tag === '390' });
				await context.close();
			}
		}
		await browser.close();
		console.log(`shots in ${OUT}`);
	} finally {
		vite.kill('SIGTERM');
	}
}
main().catch((e) => {
	console.error(e);
	process.exit(1);
});
