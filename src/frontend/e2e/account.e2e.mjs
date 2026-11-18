// The account link on brnrd.dev's board (his 11408): beside the person's name, not trailing after everything, and on
// a phone in view without scrolling the header. A local loom has no subscription, so no link.
import { test } from 'node:test';
import { deepEqual, equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';

const cloud = {
	deployment: 'cloud',
	tree: null,
	features: ['account', 'consent', 'dashboard', 'settings']
};

test('on a desktop the account link sits right after "as <name>"', async () => {
	const B = await open({ site: cloud });
	try {
		await B.page.waitForSelector('#account');
		const next = await B.page.$eval('#at', (e) => e.nextElementSibling?.id);
		equal(next, 'account');
		deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('on a phone the account link is in view beside topics', async () => {
	const B = await open({
		site: cloud,
		viewport: { width: 390, height: 844 },
		ready: '.brd header'
	});
	try {
		await B.page.waitForSelector('#account');
		const r = await B.page.$eval('#account', (e) => e.getBoundingClientRect().toJSON());
		ok(r.width > 0 && r.right <= 390, `account at ${r.left}..${r.right}`);
		deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('a local loom shows no account link', async () => {
	const B = await open({ site: { deployment: 'loopback', tree: null, features: [] } });
	try {
		equal(await B.page.locator('#account').count(), 0);
	} finally {
		await B.close();
	}
});
