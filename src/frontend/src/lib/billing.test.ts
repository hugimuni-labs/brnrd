import assert from 'node:assert/strict';
import test from 'node:test';

import {
	PRICING,
	billingReturnNotice,
	dateLabel,
	subscribeOffer,
	supporterSeatsLeft,
	withoutBillingParam
} from './billing.ts';

test('supporterSeatsLeft mirrors the pricing-page seat math', () => {
	assert.equal(supporterSeatsLeft(null), null);
	assert.equal(
		supporterSeatsLeft({ accounts: 10, supporter_seats_total: 200, supporter_seats_taken: 60 }),
		140
	);
	// oversubscribed clamps at zero, never negative
	assert.equal(
		supporterSeatsLeft({ accounts: 10, supporter_seats_total: 200, supporter_seats_taken: 230 }),
		0
	);
});

test('subscribeOffer picks supporter while seats remain or are unknown', () => {
	assert.deepEqual(subscribeOffer('monthly', 5), { usd: 5, cohort: 'supporter', label: '$5/mo' });
	assert.deepEqual(subscribeOffer('annual', 5), { usd: 50, cohort: 'supporter', label: '$50/yr' });
	// unknown stats read as open — same posture as the pricing page
	assert.equal(subscribeOffer('monthly', null).cohort, 'supporter');
	// cohort full → public prices
	assert.deepEqual(subscribeOffer('monthly', 0), { usd: 7, cohort: 'public', label: '$7/mo' });
	assert.deepEqual(subscribeOffer('annual', 0), { usd: 70, cohort: 'public', label: '$70/yr' });
});

test('pricing constants match the accepted pricing decision', () => {
	assert.equal(PRICING.supporter.monthly, 5);
	assert.equal(PRICING.supporter.annual, 50);
	assert.equal(PRICING.public.monthly, 7);
	assert.equal(PRICING.public.annual, 70);
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
	// …and the patronage steer bans credits framing from the offer surface
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
