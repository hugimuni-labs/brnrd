// Drive the LIVE brnrd.dev/daily + /ascii with the operator's session cookie.
// Read-only observation: screenshots + board text dump for judging the scene
// while a real multi-repo moment is live. Usage:
//   COOKIE=$(tr -d '\n' < ../../.tmp/brnrd_session.cookie) node repro/live-daily-shot.mjs
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

const OUT = process.env.OUT ?? '/tmp/live-daily';
const TAG = process.env.TAG ?? 'live';
const cookie = process.env.COOKIE;
if (!cookie) {
	console.error('COOKIE env required');
	process.exit(1);
}

async function main() {
	mkdirSync(OUT, { recursive: true });
	const browser = await chromium.launch();
	const context = await browser.newContext({
		viewport: { width: 1440, height: 900 },
		deviceScaleFactor: 2,
		reducedMotion: 'no-preference'
	});
	await context.addCookies([
		{
			name: 'brnrd_session',
			value: cookie,
			domain: 'brnrd.dev',
			path: '/',
			httpOnly: true,
			secure: true,
			sameSite: 'Lax'
		}
	]);
	const page = await context.newPage();

	// /daily collapsed
	await page.goto('https://brnrd.dev/daily', { waitUntil: 'networkidle' });
	await page.waitForSelector('#warp-heading', { timeout: 30000 });
	await delay(4000); // let a couple of polls land
	await page.screenshot({ path: `${OUT}/${TAG}-daily-collapsed.png` });

	// expand the stage
	const expand = page.getByRole('button', { name: 'expand the map full screen' });
	if (await expand.count()) {
		await expand.click();
		await page.waitForSelector('div[role="dialog"] pre.board', { timeout: 15000 });
		await delay(5000);
		await page.screenshot({ path: `${OUT}/${TAG}-daily-stage.png` });
		const board = await page.locator('div[role="dialog"] pre.board').textContent();
		writeFileSync(`${OUT}/${TAG}-stage-board.txt`, board ?? '');
		await page.keyboard.press('Escape');
	}

	// /ascii reference view
	await page.goto('https://brnrd.dev/ascii', { waitUntil: 'networkidle' });
	await page.waitForSelector('pre.board', { timeout: 30000 });
	await delay(5000);
	await page.screenshot({ path: `${OUT}/${TAG}-ascii.png`, fullPage: true });
	const board = await page.locator('pre.board').first().textContent();
	writeFileSync(`${OUT}/${TAG}-ascii-board.txt`, board ?? '');

	await browser.close();
	console.log(`shots → ${OUT}`);
}

main().catch((e) => {
	console.error(e);
	process.exitCode = 1;
});
