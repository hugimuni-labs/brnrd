// An agree shows its press at once and takes no second one (10099: a slow agree, pressed three times, landed three
// verdict letters). The POST is held open here, as a slow cloud would hold it.
import { after, before, test } from 'node:test';
import { equal } from 'node:assert/strict';
import { open } from './harness.mjs';

let B;
before(async () => (B = await open()));
after(() => B?.close());

test('a held agree shows sending, is disabled, and three presses send one letter', async () => {
	const posts = [];
	let release;
	const held = new Promise((r) => (release = r));
	await B.page.route('**/v1/loom/letters', async (route) => {
		posts.push(route.request().postDataJSON());
		await held;
		await route.continue();
	});
	const calls = B.page.locator('[data-column="inbox"] .calls');
	await calls.locator('.cfold').click();
	const agree = calls.locator('.call:not(.ask):not(.shut) [data-act="agree"]');
	const wish = await agree.locator('xpath=ancestor::div[@data-wish][1]').getAttribute('data-wish');
	await agree.click();
	const btn = calls.locator(`.call[data-wish="${wish}"] [data-act="agree"]`);
	await B.page.waitForFunction(
		(w) => document.querySelector(`.call[data-wish="${w}"] [data-act="agree"]`)?.disabled,
		wish
	);
	equal((await btn.innerText()).trim(), 'sending…');
	await btn.click({ force: true });
	await btn.click({ force: true });
	equal(posts.length, 1, 'one POST while the first is in flight');
	release();
	await B.page.waitForSelector(`.call.shut[data-wish="${wish}"]`);
	equal(posts.length, 1);
	equal(posts[0].wish, wish);
	equal(posts[0].verdict, 'agree');
	equal(B.errors.length, 0, B.errors.join('\n'));
});
