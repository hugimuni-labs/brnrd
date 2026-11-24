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

test('a goal shows its linked work and opens its ask and its latest delivery', async () => {
	const parts = fixture();
	const delivery = 'wish.result:bravo-v1';
	parts['thread/inbox'].board.calls.find((call) => call.wish === 'w-bravo0000000').delivery_id =
		delivery;
	parts.index.goals = [
		{
			id: 'growth',
			stands: 'No pipeline yet',
			now: 'Pilot outreach',
			next: 'A repeatable route',
			stake: 'Paying users',
			progress: { heard: 1, delivered: 1 },
			asks: [
				{
					wish: 'w-charlie0000000',
					line: 'which parser grammar?',
					blocks: 'w-alpha',
					waits: 'ada',
					at: '2026-11-24T20:00:00Z'
				}
			],
			blocked: true,
			delivery: {
				wish: 'w-bravo0000000',
				line: 'bravo happens',
				delivery_id: delivery,
				at: '2026-11-24T21:00:00Z'
			},
			moved: '2026-11-24T21:00:00Z',
			stalled: false
		}
	];
	const B = await open({ parts, viewport: { width: 390, height: 844 } });
	try {
		const { page } = B;
		const nav = page.getByRole('navigation', { name: 'Work views' });
		await nav.getByRole('button', { name: 'Goals', exact: true }).click();
		equal(await page.getByText('waits on you').isVisible(), true);
		await page.locator('summary').click();
		equal(await page.getByText('1 heard · 1 delivered', { exact: false }).isVisible(), true);

		await page.getByRole('button', { name: 'Needs you · which parser grammar?' }).click();
		equal(await page.getByRole('heading', { name: 'which parser grammar?' }).isVisible(), true);
		equal(
			await nav.getByRole('button', { name: 'Needs you 1' }).getAttribute('aria-pressed'),
			'true'
		);

		await nav.getByRole('button', { name: 'Goals', exact: true }).click();
		await page.locator('summary').click();
		await page.getByRole('button', { name: 'Latest delivery · bravo happens' }).click();
		equal(await page.getByRole('heading', { name: 'bravo happens' }).isVisible(), true);
		equal(new URL(page.url()).searchParams.get('delivery'), delivery);
		equal(await page.getByRole('button', { name: 'Accept', exact: true }).isVisible(), true);
		equal(B.errors.length, 0, B.errors.join('\n'));
	} finally {
		await B.close();
	}
});
