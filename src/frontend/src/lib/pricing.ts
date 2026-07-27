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
