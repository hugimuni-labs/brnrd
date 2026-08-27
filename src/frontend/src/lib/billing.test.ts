import assert from 'node:assert/strict';
import test from 'node:test';

import {
	PRICING,
	billingReturnNotice,
	dateLabel,
	subscribeOffer,
	withoutBillingParam
} from './billing.ts';

test('subscribeOffer always returns the single subscriber price', () => {
	assert.deepEqual(subscribeOffer('monthly'), { usd: 7, label: '$7/mo' });
	assert.deepEqual(subscribeOffer('annual'), { usd: 70, label: '$70/yr' });
});

test('pricing constants match the accepted single-price decision', () => {
	assert.equal(PRICING.monthly, 7);
	assert.equal(PRICING.annual, 70);
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
