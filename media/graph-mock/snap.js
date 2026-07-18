function loadPlaywright() {
  try {
    return require('playwright');
  } catch (_) {
    // The evaluation machine keeps Playwright in /tmp/shotwork; make the
    // artifact runnable there without a local install step.
    return require('/tmp/shotwork/node_modules/playwright');
  }
}

const { chromium } = loadPlaywright();
const path = require('path');
const url = 'file://' + path.resolve(__dirname, 'index.html');

(async () => {
  const browser = await chromium.launch();
  for (const [w, h, name] of [[1440, 900, 'desktop-1440w'], [390, 844, 'phone-390w']]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.resolve(__dirname, `${name}.png`), fullPage: true });
    console.log(`captured ${name}.png`);
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
