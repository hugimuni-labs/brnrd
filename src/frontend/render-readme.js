import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'fs';
import { dirname } from 'path';

const outDir = '/Users/gurio/Source/Projects/brnrd/.tmp/screenshots';
mkdirSync(outDir, { recursive: true });

const url = 'https://github.com/hugimuni-labs/brnrd/blob/brr/the-demo-is-on-the-readme/README.md';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  console.log(`Navigating to ${url}...`);
  await page.goto(url, { waitUntil: 'networkidle' });

  // Wait for the demo video to be present
  await page.waitForSelector('video, [role="img"]', { timeout: 10000 }).catch(() => {
    console.log('Video element selector not immediately found, proceeding anyway');
  });

  // Scroll to demo area (around line 125)
  await page.evaluate(() => {
    const readme = document.querySelector('article');
    if (readme) readme.scrollIntoView();
  });

  // Take full page screenshot
  const screenshotPath = `${outDir}/readme-demo.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(`Screenshot saved to ${screenshotPath}`);

  await browser.close();
})();
