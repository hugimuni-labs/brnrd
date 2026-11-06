import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { TAX_NOTE, disclosesTax, fetchPricing, formatEur } from './pricing.ts';
import { INSTALL_LINE } from './install.ts';

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
		.replace(/'\$lib\/install'/g, "'./install.ts'")
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

test('the paid tier on /pricing carries a tax disclosure, in euros', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('€12'), 'the premium price did not render');
	ok(html.includes(TAX_NOTE), 'the premium price rendered without the tax note');
	ok(!/\$\d/.test(html), 'a dollar figure is still on a euro page');
});

test('the page offers exactly two tiers: free with the install line, premium', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('<main'), 'the pricing content has no main landmark');
	ok(/<h1[\s>]/.test(html), 'the page has no h1');
	const plans = [...html.matchAll(/data-pricing-plan="([^"]+)"/g)].map((m) => m[1]);
	ok(plans.join(',') === 'free,premium', `expected free,premium, got ${plans.join(',')}`);
	const free = html.slice(
		html.indexOf('data-pricing-plan="free"'),
		html.indexOf('data-pricing-plan="premium"')
	);
	ok(free.includes(INSTALL_LINE), 'the free tier does not carry the install line');
	ok(free.includes('every guard and every receipt'), 'the trust product is not in free');
	ok(free.includes('unlimited repositories'), 'free still reads as capped');
});

test('premium is a founding price with the lock written on the page', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('founding price'), 'the founding price is not named');
	ok(
		html.includes('no price change for a live subscription'),
		'the lock is not stated as the rule Stripe enforces'
	);
	ok(!/annual|\/yr/i.test(html), 'an annual offer is still visible');
	ok(!/<del[\s>]/i.test(html), 'a price rendered inside a <del> element');
	ok(!/\bline-through\b/.test(html), 'a price rendered with strikethrough styling');
});

test('tokens, projects and threads are said to be never priced', async () => {
	const { body: html } = await renderRoute();
	ok(html.includes('never priced'), 'the never-priced line is missing');
	ok(/Tokens, projects, threads/.test(html), 'the never-priced list lost a member');
});

test('the page carries a search description and its own og/twitter head', async () => {
	const { head } = await renderRoute();
	ok(head.includes('name="description"'), 'pricing has no meta description');
	ok(head.includes('property="og:title"'), 'pricing has no og:title');
	ok(head.includes('property="og:description"'), 'pricing has no og:description');
	ok(head.includes('name="twitter:title"'), 'pricing has no twitter:title');
	ok(head.includes('name="twitter:description"'), 'pricing has no twitter:description');
	// canonical + og:url are +layout.svelte's job now (one computation for every
	// route, via $lib/seo's hasCanonicalMeta) rather than each page repeating a
	// literal URL — this component, rendered in isolation here, never sees its
	// own layout. seo.test.ts covers '/pricing' being canonical-eligible.
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
		public_monthly: { amount: 1200, currency: 'eur' },
		public_annual: null
	};
	const result = await fetchPricing(fakeFetch(200, payload));
	ok(result?.public_monthly?.amount === 1200);
	ok(result?.public_annual === null);
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

test('formatEur renders a whole-euro Stripe amount compactly', () => {
	ok(formatEur({ amount: 1200, currency: 'eur' }) === '€12');
});

test('formatEur keeps cents when the amount is not a whole euro', () => {
	ok(formatEur({ amount: 1250, currency: 'eur' }) === '€12.50');
});

test('formatEur refuses to relabel a leftover USD Price as euros', () => {
	ok(formatEur({ amount: 700, currency: 'usd' }) === null);
});

test('formatEur is null-safe for an absent figure', () => {
	ok(formatEur(null) === null);
	ok(formatEur(undefined) === null);
});
