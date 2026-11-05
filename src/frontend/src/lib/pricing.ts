// Price-display copy, in a module rather than inline in the route, so the
// one line that carries a legal duty has somewhere a test can reach it.
//
// The pricing page advertises one figure, 12 EUR/mo, VAT included (the
// 2026-11-05 proposal, brnrd-self threads/pricing/design.md). C. conso art.
// L112-1 with the arrêté du 3 décembre 1987 wants a consumer shown the total
// payable, toutes taxes comprises and en euros: the Stripe Price carries
// tax_behavior=inclusive in EUR, so the figure on this page is the figure
// paid. The earlier USD, ex-tax figures ($7/mo) left the euro limb open;
// this closes it.
//
// The *pre-contractual* duty (Dir. 2011/83/EU art. 6(1)(e)) is met by
// Stripe's hosted checkout, which shows the total before you are bound.
// If Managed Payments ever refuses an inclusive Price, the Price goes
// exclusive and this note must say "excl. VAT" again: the note describes
// the Price, it does not choose it.
export const TAX_NOTE = 'VAT included — the price you pay (France: 20%)';

/** Does a price-display note actually disclose a tax? */
export function disclosesTax(note: string): boolean {
	return /\b(VAT|TVA|tax|taxes)\b/i.test(note) && /\d+\s*%/.test(note);
}

// Stripe-derived figures (#831): `GET /v1/stats/pricing` reads the live
// Price objects checkout actually charges, so the numbers on this page and
// the numbers on the invoice cannot silently drift apart. The supporter
// fields remain in the response for compatibility with historical Stripe
// subscriptions, but the current offer reads only the public figures.
// One currency per Price, deliberately not location-aware — see the
// endpoint's own docstring (`src/brnrd/routers/stats.py`) and #831.
// A figure reads `null` when Stripe is unreachable or unconfigured; callers
// keep the baked-in literal as their floor, same failure posture as
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
 * uses (€12, not €12.00) — falls back to nothing on a non-EUR figure so a
 * leftover USD Price doesn't silently relabel itself as euros. */
export function formatEur(figure: PriceFigure | null | undefined): string | null {
	if (!figure || figure.currency !== 'eur') return null;
	const euros = figure.amount / 100;
	return Number.isInteger(euros) ? `€${euros}` : `€${euros.toFixed(2)}`;
}
