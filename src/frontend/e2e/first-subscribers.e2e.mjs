// The first paying subscribers see the install-session link (his 11426): in the notice right after Stripe sends them
// back to /account, and on /account while they stay subscribed. The server decides who (billing.install_session_link,
// tests/test_first_subscribers.py); here the page is driven with the read the server sends.
import { test } from 'node:test';
import { equal, deepEqual } from 'node:assert/strict';
import { open } from './harness.mjs';

const LINK = 'https://calendly.com/alexandra-hugimuni/hugimuni-sas';
const SHOTS = process.env.BRNRD_E2E_SHOTS || '';
const cloud = { deployment: 'cloud', tree: null, features: ['account', 'consent', 'dashboard', 'settings'] };

const subscribed = (url) => ({
	tier: 'subscribed',
	status: 'active',
	cohort: 'public',
	cadence: 'monthly',
	cancel_at_period_end: false,
	current_period_end: '2026-12-18T22:00:00',
	install_session_url: url,
	first_subscribers_seats: url ? 20 : null
});
const free = { tier: 'free', status: null, cohort: null, cadence: null, cancel_at_period_end: false, current_period_end: null };

// `reads` answers /v1/accounts/subscription in turn, the last one for good.
async function account(path, reads, viewport) {
	const B = await open({ site: cloud, ready: '.brd header', ...(viewport ? { viewport } : {}) });
	let n = 0;
	const json = (body) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
	await B.page.route('**/v1/dashboard/repos*', (r) => r.fulfill(json({ account: { github_login: 'ada' }, repos: [] })));
	await B.page.route('**/v1/stats/pricing', (r) => r.fulfill(json({ public_monthly: { amount: 700, currency: 'usd' } })));
	await B.page.route('**/v1/accounts/subscription', (r) => r.fulfill(json(reads[Math.min(n++, reads.length - 1)])));
	await B.page.goto(B.base + path.replace(/^\//, ''));
	await B.page.waitForSelector('.acct .panel');
	return B;
}

const offers = (B) =>
	B.page.$$eval('.install-offer', (els) =>
		els.map((e) => ({ text: e.textContent.replace(/\s+/g, ' ').trim(), href: e.querySelector('a')?.href, inNotice: !!e.closest('[role=status]') }))
	);

test('back from checkout, a first subscriber reads the link in the notice once the webhook lands', async () => {
	const B = await account('/account?billing=subscribed', [free, subscribed(LINK)]);
	try {
		await B.page.waitForSelector('[role=status] .install-offer', { timeout: 15_000 });
		deepEqual(await offers(B), [
			{ text: "You're one of our first 20 subscribers. Book a free install session with us.", href: LINK, inNotice: true }
		]);
		if (SHOTS) await B.page.screenshot({ path: `${SHOTS}/after-checkout-desktop.png` });
		deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('on /account a first subscriber reads the link beside the subscription', async () => {
	for (const [name, viewport] of [['desktop', null], ['phone', { width: 390, height: 844 }]]) {
		const B = await account('/account', [subscribed(LINK)], viewport);
		try {
			await B.page.waitForSelector('.install-offer');
			deepEqual(await offers(B), [
				{ text: "You're one of our first 20 subscribers. Book a free install session with us.", href: LINK, inNotice: false }
			]);
			if (SHOTS) await B.page.screenshot({ path: `${SHOTS}/account-${name}.png` });
			deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	}
});

test('a subscriber past the first seats, and a free account, see no link', async () => {
	for (const read of [subscribed(null), free]) {
		const B = await account('/account?billing=subscribed', [read]);
		try {
			await B.page.waitForSelector('.acct .panel p.font-mono');
			equal(await B.page.locator('.install-offer').count(), 0);
		} finally {
			await B.close();
		}
	}
});
