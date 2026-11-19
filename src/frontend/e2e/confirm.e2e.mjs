import { equal } from 'node:assert/strict';
import { test } from 'node:test';
import { open } from './harness.mjs';

const id = 'c-confirmation';
const approval = {
	request: {
		id,
		what: 'Send the contract',
		thread: 'contract',
		digest: 'a'.repeat(64),
		ask_exp: '2099-01-01T15:00:00Z'
	},
	why: 'You asked me to send it',
	call: { command: 'send-contract contract.pdf' },
	status: 'pending',
	can_press: true
};

for (const [deployment, api, button, decision, status, result] of [
	['loopback', '/v1/loom', 'Do it', 'approve', 'given', 'Done: you said do it'],
	['cloud', '/v1/dashboard/loom', 'Don’t', 'decline', 'refused', 'You said don’t']
]) {
	test(`${deployment} confirmation shows the exact call and submits one decision`, async () => {
		const B = await open({ site: { deployment, tree: null, features: [] }, ready: 'body' });
		try {
			const { page } = B;
			let posted = 0;
			await page.route(`**${api}/confirms/${id}`, async (route) => {
				if (route.request().method() === 'GET') return route.fulfill({ json: approval });
				posted += 1;
				equal(route.request().postDataJSON().decision, decision);
				await route.fulfill({
					json: { ...approval, status, pressed_at: '2098-12-31T16:32:00Z' }
				});
			});
			await page.goto(new URL(`/confirm/${id}`, B.base).href);
			await page.getByRole('heading', { name: approval.request.what }).waitFor();
			await page.getByText('Exact call').click();
			await page.getByText(approval.call.command, { exact: true }).waitFor();
			await page.getByRole('button', { name: button }).click();
			await page.getByText(result).waitFor();
			equal(posted, 1);
			equal(await page.getByRole('button', { name: 'Do it' }).count(), 0);
		} finally {
			await B.close();
		}
	});
}
