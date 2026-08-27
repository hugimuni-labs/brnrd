import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { TAX_NOTE, disclosesTax, fetchPricing, formatUsd } from './pricing.ts';

const here = dirname(fileURLToPath(import.meta.url));
const routePath = join(here, '..', 'routes', 'pricing', '+page.svelte');

// Same shape as legalNotice.test.ts, and for the same reason: string-matching
// the source would pass on a page that imports the constant and never renders
// it. These assertions run against the HTML the component actually emits.
async function renderRoute(): Promise<ReturnType<typeof render>> {
	const source = readFileSync(routePath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'PricingPage' });
	const generated = join(here, '.pricingRoute.generated.mjs');
	const runnable = compiled.js.code
		.replace(/'\$lib\/pricing'/g, "'./pricing.ts'")
		.replace(/'\$lib\/legalNotice'/g, "'./legalNotice.ts'")
		.replace(/'\$lib\/publicStats'/g, "'./publicStats.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default);
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(join(here, '.pricingRoute.generated.mjs'), { force: true }));

test('the single paid tier on /pricing carries a tax disclosure', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('$7'), 'the subscriber price did not render');
	ok(html.includes(TAX_NOTE), 'the subscriber price rendered without the tax note');
});

test('the hosted offer names its real product boundaries and live routes', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('one connected repository'), 'the free repository allowance is not visible');
	ok(html.includes('WhatsApp'), 'the live hosted WhatsApp route is missing from the offer');
	ok(
		html.includes('one-repository product cap is removed'),
		'the paid repository entitlement is still expressed as an unspecified “more”'
	);
	ok(
		html.includes('free-tier hosted event limits are removed'),
		'the paid event entitlement is not observable'
	);
	ok(
		html.includes('limit lifts are live now'),
		'the page still presents a live entitlement as pending'
	);
	ok(
		!html.includes('entitlements are still landing'),
		'stale pre-entitlement caveat is still rendered'
	);
});

test('the page compares hosted tiers and separates deployment from patronage', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('<main'), 'the pricing content has no main landmark');
	ok(/<h1[\s>]/.test(html), 'the page has no h1');
	const hostedPlans = html.match(/data-pricing-plan=/g) ?? [];
	ok(hostedPlans.length === 2, `expected two comparable hosted plans, got ${hostedPlans.length}`);
	ok(
		html.includes('Self-hosting is a deployment path'),
		'self-hosting still reads as a third tier'
	);
	ok(html.includes('support the commons'), 'contributor patronage is not separated from pricing');
	ok(
		!html.includes('premium contributor bundle'),
		'sponsorship still reads as a premium product tier'
	);
});

test('the page carries a search description and canonical URL', async () => {
	const { head } = await renderRoute();
	ok(head.includes('name="description"'), 'pricing has no meta description');
	ok(head.includes('https://brnrd.dev/pricing'), 'pricing has no canonical URL');
});

test('the subscriber offer has one price and no founder-cohort language', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('$7'), 'monthly subscriber price is missing');
	ok(html.includes('$70'), 'annual subscriber price is missing');
	ok(!html.includes('$5'), 'retired founder price is still visible');
	ok(!/founding price|first 200|first \d+ subscriptions|locked while active|later \$?/i.test(html));
	ok(!/<del[\s>]/i.test(html), 'subscriber price rendered inside a <del> element');
	ok(!/\bline-through\b/.test(html), 'subscriber price rendered with strikethrough styling');
});

test('the tax note names both a tax and a rate', () => {
	// The property, not the wording: copy gets rewritten and a test that pins
	// a sentence fires on a reflow instead of on a real removal.
	ok(disclosesTax(TAX_NOTE));
});

test('disclosesTax rejects a note that omits the tax or the rate', () => {
	ok(!disclosesTax('prices at checkout are set by Stripe and shown before you pay'));
	ok(!disclosesTax('taxes may apply'));
	ok(!disclosesTax('20% off for the first cohort'));
});

// --- Stripe-derived figures (#831) -------------------------------------------

function fakeFetch(status: number, body: unknown): typeof fetch {
	return (async () => ({
		ok: status >= 200 && status < 300,
		status,
		json: async () => body
	})) as unknown as typeof fetch;
}

test('fetchPricing returns the parsed payload on success', async () => {
	const payload = {
		supporter_monthly: { amount: 500, currency: 'usd' },
		supporter_annual: null,
		public_monthly: { amount: 700, currency: 'usd' },
		public_annual: { amount: 7000, currency: 'usd' }
	};
	const result = await fetchPricing(fakeFetch(200, payload));
	ok(result?.public_monthly?.amount === 700);
	ok(result?.public_annual?.amount === 7000);
});

test('fetchPricing degrades to null on a non-2xx response, never throws', async () => {
	ok((await fetchPricing(fakeFetch(500, {}))) === null);
});

test('fetchPricing degrades to null on a network failure, never throws', async () => {
	const throws = (async () => {
		throw new Error('offline');
	}) as unknown as typeof fetch;
	ok((await fetchPricing(throws)) === null);
});

test('formatUsd renders a whole-dollar Stripe amount compactly', () => {
	ok(formatUsd({ amount: 700, currency: 'usd' }) === '$7');
});

test('formatUsd keeps cents when the amount is not a whole dollar', () => {
	ok(formatUsd({ amount: 750, currency: 'usd' }) === '$7.50');
});

test('formatUsd refuses to mislabel a non-USD figure as dollars', () => {
	ok(formatUsd({ amount: 700, currency: 'eur' }) === null);
});

test('formatUsd is null-safe for an absent figure', () => {
	ok(formatUsd(null) === null);
	ok(formatUsd(undefined) === null);
});
