import assert from 'node:assert/strict';
import test from 'node:test';

import {
	PRICING,
	SUBSCRIBE_PITCH,
	billingReturnNotice,
	dateLabel,
	installSessionOffer,
	subscribeOffer,
	withoutBillingParam
} from './billing.ts';
import { TAX_NOTE } from './pricing.ts';

test('subscribeOffer falls back to the €12 literal, VAT included, with no live figure', () => {
	assert.deepEqual(subscribeOffer(), {
		cadence: 'monthly',
		label: '€12/mo',
		taxNote: TAX_NOTE
	});
	assert.equal(subscribeOffer(null).label, '€12/mo');
});

test('subscribeOffer labels the button with the Price checkout charges', () => {
	// Production today: the public monthly Price is 700 usd (2026-11-17).
	const usd = subscribeOffer({ amount: 700, currency: 'usd' });
	assert.equal(usd.label, '$7/mo');
	assert.ok(!/VAT included/.test(usd.taxNote), 'VAT included beside a USD figure');
	const eur = subscribeOffer({ amount: 1200, currency: 'eur' });
	assert.equal(eur.label, '€12/mo');
	assert.equal(eur.taxNote, TAX_NOTE);
});

test('the pitch promises no trial and no free repository', () => {
	assert.ok(/hosted limits/.test(SUBSCRIBE_PITCH));
	assert.ok(!/trial|first repository|stays free/i.test(SUBSCRIBE_PITCH));
});

test('pricing constants match the 2026-11-05 decision: monthly only', () => {
	assert.equal(PRICING.monthly, 12);
	assert.ok(!('annual' in PRICING), 'an annual figure is back in the offer');
});

test('billingReturnNotice maps the four return params and nothing else', () => {
	assert.equal(billingReturnNotice('?billing=subscribed')?.kind, 'success');
	assert.equal(billingReturnNotice('?billing=topup-complete')?.kind, 'success');
	assert.equal(billingReturnNotice('?billing=canceled')?.kind, 'quiet');
	assert.equal(billingReturnNotice('?billing=topup-canceled')?.kind, 'quiet');
	assert.equal(billingReturnNotice('?billing=nonsense'), null);
	assert.equal(billingReturnNotice(''), null);
	// success copy stays honest about webhook-paced entitlements…
	assert.match(billingReturnNotice('?billing=subscribed')!.text, /incrementally/);
	assert.doesNotMatch(billingReturnNotice('?billing=subscribed')!.text, /active|done/i);
	// …and the offer surface bans credits framing
	for (const param of ['subscribed', 'topup-complete', 'canceled', 'topup-canceled']) {
		assert.doesNotMatch(billingReturnNotice(`?billing=${param}`)!.text, /credit/i);
	}
});

test('withoutBillingParam strips only the billing param', () => {
	assert.equal(withoutBillingParam('https://brnrd.dev/?billing=subscribed'), '/');
	assert.equal(withoutBillingParam('https://brnrd.dev/?billing=canceled&x=1#frag'), '/?x=1#frag');
	assert.equal(withoutBillingParam('https://brnrd.dev/repos?x=1'), '/repos?x=1');
});

test('dateLabel renders compact UTC dates and degrades to empty', () => {
	assert.equal(dateLabel('2026-07-21T10:15:00Z'), '2026-07-21');
	assert.equal(dateLabel('2026-12-31T23:59:59Z'), '2026-12-31');
	assert.equal(dateLabel('not a date'), '');
});

test('installSessionOffer is the server-set link with the seat count, or nothing', () => {
	const base = {
		tier: 'subscribed',
		status: 'active',
		cohort: 'public',
		cadence: 'monthly',
		cancel_at_period_end: false,
		current_period_end: null
	};
	assert.equal(installSessionOffer(null), null);
	assert.equal(installSessionOffer(base), null);
	assert.equal(installSessionOffer({ ...base, install_session_url: null }), null);
	assert.deepEqual(
		installSessionOffer({
			...base,
			install_session_url: 'https://cal.example/x',
			first_subscribers_seats: 20
		}),
		{
			lead: "You're one of our first 20 subscribers.",
			link: 'Book a free install session with us',
			url: 'https://cal.example/x'
		}
	);
	assert.equal(
		installSessionOffer({ ...base, install_session_url: 'https://cal.example/x' })?.lead,
		"You're one of our first subscribers."
	);
});
