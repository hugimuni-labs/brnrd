// Price-display copy, in a module rather than inline in the route, so the
// one line that carries a legal duty has somewhere a test can reach it.
//
// The pricing page advertises ex-tax figures ($5/mo, $7/mo, $50/yr, $70/yr).
// A French buyer pays $6.00 for the $5 tier — driven 2026-07-27 against the
// live Stripe checkout: `Subtotal US$5.00 · TVA (20%) US$1.00 · Total due
// today US$6.00`. C. conso art. L112-1 with the arrêté du 3 décembre 1987
// wants a consumer shown the total payable, toutes taxes comprises.
//
// The *pre-contractual* duty (Dir. 2011/83/EU art. 6(1)(e)) is already met —
// Stripe's page shows the tax-inclusive total before you are bound. The duty
// this closes is the advertising one, on our own page, which carried no tax
// word anywhere.
//
// Still open, deliberately not decided here: the same arrêté wants prices
// *en euros*, and every figure is USD. See
// `subject-legal-compliance.md → Price display`.
export const TAX_NOTE = 'excl. VAT — your country’s rate is added at checkout (France: 20%)';

/** Does a price-display note actually disclose a tax? */
export function disclosesTax(note: string): boolean {
	return /\b(VAT|TVA|tax|taxes)\b/i.test(note) && /\d+\s*%/.test(note);
}

// Stripe-derived figures (#831): `GET /v1/stats/pricing` reads the live
// Price objects checkout actually charges, so the numbers on this page and
// the numbers on the invoice cannot silently drift apart. USD only, and
// deliberately not location-aware — see the endpoint's own docstring
// (`src/brnrd/routers/stats.py`) and #831's design report for why. A tier
// reads `null` when Stripe is unreachable or unconfigured; every caller
// keeps the baked-in literal as its floor, same failure posture as
// `fetchPublicStats` in `publicStats.ts`.

export interface PriceFigure {
	amount: number;
	currency: string;
}

export interface PricingFigures {
	supporter_monthly: PriceFigure | null;
	supporter_annual: PriceFigure | null;
	public_monthly: PriceFigure | null;
	public_annual: PriceFigure | null;
}

export async function fetchPricing(fetcher: typeof fetch = fetch): Promise<PricingFigures | null> {
	try {
		const resp = await fetcher('/v1/stats/pricing');
		if (!resp.ok) return null;
		return (await resp.json()) as PricingFigures;
	} catch {
		// Decoration, not a gate — a fetch failure renders as the baked-in
		// literal, never as an error state the visitor must read.
		return null;
	}
}

/** A Stripe minor-unit amount as the compact display string this page
 * uses ($5, not $5.00) — falls back to nothing on a non-USD figure so a
 * future currency doesn't silently mislabel itself as dollars. */
export function formatUsd(figure: PriceFigure | null | undefined): string | null {
	if (!figure || figure.currency !== 'usd') return null;
	const dollars = figure.amount / 100;
	return Number.isInteger(dollars) ? `$${dollars}` : `$${dollars.toFixed(2)}`;
}
