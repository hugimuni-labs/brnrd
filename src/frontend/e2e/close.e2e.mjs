// The bead sheet closes at any moment (w-h7ik3bznilu5, his 10376/10377): × pressed while the beads part is still in
// flight closes it at once, and the part landing later neither reopens it nor loses itself; the next press paints from
// the held part with no wait.
import { after, before, test } from 'node:test';
import { deepEqual, equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';

let B,
	release = () => {},
	asked = 0;
before(async () => {
	B = await open();
	await B.page.route('**/v1/loom/beads', async (route) => {
		asked++;
		await new Promise((r) => (release = r));
		await route.continue();
	});
});
after(() => B?.close());

const sheetOn = () => B.page.evaluate(() => !!document.querySelector('#bead.on'));

test('× closes the sheet before the beads land, and their landing leaves it closed', async () => {
	const id = await B.page.evaluate(
		() =>
			[
				...document.querySelectorAll(
					'.brd .column.threadcol article.letter.resident:not(.collapsed)'
				)
			].find((e) => e.offsetParent).dataset.letter
	);
	const card = B.page.locator(`.brd .column.threadcol article.letter[data-letter="${id}"]`).first();
	await card.click({ position: { x: 20, y: 8 } });
	await B.page.waitForSelector('#bead.on');
	equal(asked, 1, 'the press asked for the beads');
	await B.page.locator('#bead .bh button.x').click();
	await B.page.waitForSelector('#bead:not(.on)', { timeout: 2000 });
	const landed = B.page.waitForResponse('**/v1/loom/beads');
	release();
	await landed;
	await B.page.waitForTimeout(400);
	equal(await sheetOn(), false, 'the beads landing did not reopen it');

	const t0 = Date.now();
	await card.click({ position: { x: 20, y: 8 } });
	await B.page.waitForSelector(`#bead.on:not(.loading) .bd.me[data-bead="${id}"]`, {
		timeout: 2000
	});
	ok(Date.now() - t0 < 2000);
	equal(asked, 1, 'the held part is used, not fetched again');
	await B.page.keyboard.press('Escape');
	await B.page.waitForSelector('#bead:not(.on)');
	deepEqual(B.errors, []);
});
