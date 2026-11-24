import { test } from 'node:test';
import { equal } from 'node:assert/strict';
import { fixture } from './fixture.mjs';
import { open } from './harness.mjs';

test('work views stay in reach on a phone and preserve the activity board', async () => {
	const parts = fixture();
	parts.index.goals = [
		{
			id: 'paid-subscriptions',
			stands: 'Find pilot prospects',
			now: 'Researching live projects',
			next: 'Invite one pilot',
			stake: 'Find a repeatable route to paid subscriptions'
		}
	];
	const delivery = 'wish.result:bravo-v1';
	parts['thread/inbox'].board.calls.find((call) => call.wish === 'w-bravo0000000').delivery_id =
		delivery;
	const B = await open({ parts, viewport: { width: 390, height: 844 } });
	try {
		const { page } = B;
		const nav = page.getByRole('navigation', { name: 'Work views' });
		const scroll = await page.locator('#board').evaluate((board) => {
			board.scrollLeft = 350;
			return board.scrollLeft;
		});
		await nav.getByRole('button', { name: 'Goals', exact: true }).click();
		await page.locator('summary').click();
		equal(await page.getByText('Researching live projects').isVisible(), true);

		await nav.getByRole('button', { name: 'Needs you 1' }).click();
		await page.getByRole('button', { name: 'which parser grammar?' }).click();
		await page.getByRole('button', { name: 'Wish history' }).click();
		await page.waitForSelector('.brd.sheet');
		await nav.getByRole('button', { name: 'Ready 1' }).click();
		await page.getByRole('button', { name: 'bravo happens' }).click();
		equal(await page.getByText('threads/bravo/README.md', { exact: true }).isVisible(), true);

		let sent;
		await page.route('**/v1/loom/letters', async (route) => {
			sent = route.request().postDataJSON();
			await route.fulfill({ json: { id: 'loom:ada:review', thread: 'inbox' } });
		});
		await page.getByRole('button', { name: 'Accept', exact: true }).click();
		await page.getByText('Decision · sent').waitFor();
		equal(sent.delivery_id, delivery);

		await page.getByRole('button', { name: '← Board' }).click();
		equal(await page.locator('#board').evaluate((board) => board.scrollLeft), scroll);
		equal(B.errors.length, 0, B.errors.join('\n'));
	} finally {
		await B.close();
	}
});
