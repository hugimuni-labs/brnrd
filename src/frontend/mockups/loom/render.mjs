import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';
import { closeBrowser, runDriver } from '../../repro/finish.mjs';

const root = new URL('.', import.meta.url);
const out = new URL('./renders/', root);
const pages = [
	'desktop-now.html',
	'desktop-raised.html',
	'desktop-crew.html',
	'phone-watch.html',
	'phone-map.html'
];
const sizes = [
	{ name: '1280x800', width: 1280, height: 800 },
	{ name: '390x844', width: 390, height: 844 }
];

async function main() {
	await mkdir(out, { recursive: true });
	const browser = await chromium.launch();
	try {
		for (const pageName of pages)
			for (const size of sizes) {
				const page = await browser.newPage({ viewport: size });
				await page.goto(new URL(pageName, root).href, { waitUntil: 'networkidle' });
				const path = new URL(`./renders/${pageName.replace('.html', '')}-${size.name}.png`, root);
				await page.screenshot({ path: path.pathname, fullPage: false });
				console.log(path.pathname);
				await page.close();
			}
	} finally {
		await closeBrowser(browser, 'loom-render');
	}
}
runDriver(main);
