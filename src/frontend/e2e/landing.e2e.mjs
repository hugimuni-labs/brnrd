// The signed-out landing on a phone (threads/landing-phone in brnrd-self): at 390x844 the h1's type-reveal ends on
// the headline even when frames stop arriving, and the install line shows through `| sh` without a hidden scroll.
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';

const HEADLINE = 'Message it the work. Get back a pull request.';
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true };

let B;
before(async () => (B = await open()));
after(() => B?.close());

async function landing(options = {}, init = null) {
	const ctx = await B.browser.newContext({ ...PHONE, ...options }),
		page = await ctx.newPage();
	if (init) await page.addInitScript(init);
	await page.route('**/v1/loom/**', (r) => r.fulfill({ status: 401, body: '{}' }));
	await page.route('**/v1/dashboard/**', (r) => r.fulfill({ status: 401, body: '{}' }));
	await page.goto(B.base);
	await page.locator('[data-landing] h1').waitFor();
	return { ctx, page };
}

const h1 = (page) =>
	page.evaluate(() => document.querySelector('[data-landing] h1').textContent.trim());

// What the round-4 shell did: the reveal draws its first frame, then rAF never fires again. Every frame requested
// once the h1 holds its per-character cells is dropped after the first, so only the timer fallback can settle it.
function starveFramesAfterTheFirstDraw() {
	const raf = window.requestAnimationFrame.bind(window);
	let drawn = 0;
	window.requestAnimationFrame = (cb) => {
		const building = document.querySelector('[data-landing] h1 span');
		if (building && drawn++ >= 1) return 0;
		return raf(cb);
	};
}

test('the h1 ends on the headline even when frames stop after the first draw', async () => {
	const { ctx, page } = await landing({}, starveFramesAfterTheFirstDraw);
	await page.waitForFunction(() => document.querySelector('[data-landing] h1 span'), null, {
		polling: 100,
		timeout: 10_000
	});
	await page.waitForFunction(
		(want) => document.querySelector('[data-landing] h1').textContent.trim() === want,
		HEADLINE,
		// Interval polling: waitForFunction's default polls on rAF, which this test starves.
		{ polling: 100, timeout: 10_000 }
	);
	assert.equal(await page.locator('[data-landing] h1 span').count(), 0, 'no cell scaffolding left');
	await ctx.close();
});

test('with reduced motion the h1 is the headline from the first look', async () => {
	const { ctx, page } = await landing({ reducedMotion: 'reduce' });
	assert.equal(await h1(page), HEADLINE);
	await page.waitForTimeout(3000);
	assert.equal(await h1(page), HEADLINE);
	assert.equal(await page.locator('[data-landing] h1 span').count(), 0);
	await ctx.close();
});

test('both install lines show through `| sh` at 390 wide, wrapped rather than scrolled', async () => {
	const { ctx, page } = await landing();
	const lines = await page.$$eval('[data-landing] pre', (pres) =>
		pres
			.filter((p) => p.textContent.includes('brnrd.dev/install'))
			.map((p) => ({
				text: p.textContent,
				hidden: p.scrollWidth - p.clientWidth,
				right: p.getBoundingClientRect().right
			}))
	);
	assert.equal(lines.length, 2, 'the hero line and the install section line');
	for (const line of lines) {
		assert.match(line.text, /curl -fsSL https:\/\/brnrd\.dev\/install \| sh/);
		assert.ok(line.hidden <= 0, `nothing scrolled out of view (${line.hidden}px hidden)`);
		assert.ok(line.right <= 390, `inside the viewport (right edge ${line.right})`);
	}
	assert.ok(
		(await page.evaluate(() => document.documentElement.scrollWidth)) <= 390,
		'the page itself does not scroll sideways'
	);
	await ctx.close();
});
