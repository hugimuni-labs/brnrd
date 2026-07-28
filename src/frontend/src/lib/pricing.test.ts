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
async function renderRoute(): Promise<string> {
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
		return render(module.default).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(join(here, '.pricingRoute.generated.mjs'), { force: true }));

test('every paid tier on /pricing carries a tax disclosure', async () => {
	const html = await renderRoute();
	// Both paid tiers are behind `{#if supporterOpen}` / `{:else}`, so exactly
	// one renders at a time — but whichever one does must be priced honestly.
	// A regression that dropped the note from only the second branch would
	// otherwise stay invisible until the 200th account signed up.
	const paidPrices = ['$5', '$7'].filter((price) => html.includes(price));
	ok(paidPrices.length > 0, 'no paid tier rendered — the test is asserting nothing');
	ok(html.includes(TAX_NOTE), 'a paid price rendered without the tax note');
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
		public_monthly: null,
		public_annual: null
	};
	const result = await fetchPricing(fakeFetch(200, payload));
	ok(result?.supporter_monthly?.amount === 500);
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
	ok(formatUsd({ amount: 500, currency: 'usd' }) === '$5');
});

test('formatUsd keeps cents when the amount is not a whole dollar', () => {
	ok(formatUsd({ amount: 550, currency: 'usd' }) === '$5.50');
});

test('formatUsd refuses to mislabel a non-USD figure as dollars', () => {
	ok(formatUsd({ amount: 500, currency: 'eur' }) === null);
});

test('formatUsd is null-safe for an absent figure', () => {
	ok(formatUsd(null) === null);
	ok(formatUsd(undefined) === null);
});
